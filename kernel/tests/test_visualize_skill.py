# -*- coding: utf-8 -*-
"""The visual skill validates before side effects and keeps an offline result."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from kernel.plugins import host_actions
from kernel.plugins.grants import GrantStore
from kernel.plugins.host import ServiceError
from kernel.plugins.manifest import load_manifest, package_digest
from kernel.plugins.registry import SkillRegistry
from kernel.plugins.services import CoreServices
from kernel.skills_bundled.visualize.renderer import ROOT, render_page, validate_page
from kernel.tests.skill_fixtures import grant_all


def example():
    return {"title": "Архитектура и данные", "language": "ru", "demo": True, "sections": [
        {"type": "diagram", "title": "Поток запроса", "nodes": [
            {"id": "a", "label": "API", "column": 0, "row": 0, "detail": "Validate input"},
            {"id": "b", "label": "Database", "column": 1, "row": 0}],
         "edges": [{"from": "a", "to": "b", "label": "Query"}]},
        {"type": "chart", "title": "Trend", "kind": "line", "labels": ["One", "Two", "Three"],
         "series": [{"name": "Tasks", "values": [-1, 0, 5]}]},
        {"type": "scene3d", "title": "Layout", "objects": [
            {"id": "x", "label": "Gateway", "shape": "sphere", "position": [-3, 0, 0]},
            {"id": "y", "label": "Worker", "shape": "box", "position": [0, 2, 1]},
            {"id": "z", "label": "Store", "shape": "cone", "position": [3, 0, -1]}],
         "edges": [{"from": "x", "to": "y"}, {"from": "y", "to": "z"}]},
        {"type": "table", "title": "Measurements", "columns": ["Name", "Value"], "rows": [["A", 0], ["B", None]]},
        {"type": "metrics", "title": "Count", "items": [{"label": "Observed", "value": "3"}]},
        {"type": "text", "title": "Notes", "body": "Illustrative only."},
    ]}


@pytest.mark.parametrize("kind", ["line", "bar", "donut"])
def test_every_chart_renders_from_one_source_of_truth(kind):
    page = example()
    page['sections'][1].update(kind=kind, series=[{'name': 'Tasks', 'values': [0, 3, 7]}])
    output = render_page(page)
    assert validate_page(page) == []
    payload = re.search(r'<script type="application/json" id="page-data">(.*?)</script>', output, re.S)[1]
    assert json.loads(payload) == page
    assert '<td>3</td>' in output and '<td>7</td>' in output
    assert '<canvas' in output and '<svg' in output
    assert 'Демонстрационные данные' in output


@pytest.mark.parametrize("modify, message", [
    (lambda p: p['sections'][0]['edges'][0].update(to='missing'), 'unknown endpoint'),
    (lambda p: p['sections'][0]['nodes'][1].update(id='a'), 'duplicate id'),
    (lambda p: p['sections'][0]['nodes'][1].update(column=0), 'same column/row'),
    (lambda p: p['sections'][1]['series'][0].update(values=[1]), 'one value'),
    (lambda p: p['sections'][1].update(kind='donut'), 'non-negative'),
    (lambda p: p['sections'][1]['series'][0].update(values=[float('nan')]), 'finite'),
    (lambda p: p['sections'][2]['objects'][0].update(position=[0, 1]), 'at least 3'),
    (lambda p: p['sections'][2]['objects'][0].update(size=-1), 'must be >='),
    (lambda p: p['sections'][2]['objects'][0].update(shape='script'), 'must be one'),
    (lambda p: p['sections'][2]['edges'][0].update(to='missing'), 'unknown endpoint'),
    (lambda p: p['sections'][3]['rows'].append([1]), 'expected 2 cells'),
    (lambda p: p.update(html='<script>'), 'unexpected field'),
])
def test_bad_descriptions_have_actionable_errors(modify, message):
    page = example()
    modify(page)
    assert message in ' '.join(validate_page(page))
    with pytest.raises(ValueError):
        render_page(page)


@pytest.mark.parametrize('page', [None, [], {'title': 'x', 'sections': []}, {'title': '\ud800', 'sections': []}])
def test_invalid_page_types_are_errors_not_crashes(page):
    assert validate_page(page)


def test_model_text_never_becomes_markup_or_javascript_and_script_hash_matches():
    page = example()
    attack = '</script><script>alert(1)</script><img src=x onerror=alert(2)>'
    page['title'] = attack
    page['sections'][0]['nodes'][0]['detail'] = attack
    output = render_page(page)
    assert attack not in output
    assert '&lt;/script&gt;' in output
    script = re.search(r'<script>(.*?)</script>', output, re.S)[1]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in output and "connect-src 'none'" in output
    assert '<script src=' not in output and '<link ' not in output


def test_repeated_diagrams_have_unique_markers():
    page = example()
    page['sections'].append(copy.deepcopy(page['sections'][0]))
    output = render_page(page)
    ids = re.findall(r'\bid="([^"]+)"', output)
    assert len(ids) == len(set(ids))
    assert 'url(#arrow-0)' in output and 'url(#arrow-6)' in output


@pytest.fixture
def visual_agent(agent_factory, tmp_path, monkeypatch):
    monkeypatch.setenv('KVIST_CONFIG_DIR', str(tmp_path / 'config'))
    agent = agent_factory()
    registry = SkillRegistry(tmp_path / 'config' / 'plugins' / 'skill')
    installed = registry.install(ROOT, origin='bundled')
    registry.activate(installed.skill_id, installed.version, installed.digest)
    agent.skills.registry = registry
    agent.skills.grants = GrantStore(tmp_path / 'grants.json')
    grant_all(agent.skills.grants, registry, 'visualize', agent.executor.workspace)
    agent._run_id = 'visual-test'
    agent.skills.attach('visualize')
    return agent


def call(agent, tool='show', **arguments):
    return agent.skills.call('visualize', tool, arguments or {'page': example()}, 'call')


def test_show_opens_only_the_generated_artifact_and_it_survives_task_cleanup(visual_agent, monkeypatch):
    opened = []
    monkeypatch.setattr(host_actions, 'open_visualization', opened.append)
    result = call(visual_agent)['data']['result']
    path = Path(result['artifact']['path'])
    assert result['valid'] and result['opened'] and opened == [path]
    assert path.read_text().startswith('<!doctype html>')
    assert visual_agent.skills.attached['visualize'].host is None
    visual_agent.skills.finish_task('done')
    assert path.exists()


def test_invalid_input_and_validation_create_no_artifacts_or_browser(visual_agent, monkeypatch):
    opened = []
    monkeypatch.setattr(host_actions, 'open_visualization', opened.append)
    assert call(visual_agent, 'validate')['data']['result']['valid']
    result = call(visual_agent, page={'title': 'Broken', 'sections': []})['data']['result']
    assert not result['valid'] and not result['opened'] and opened == []
    assert visual_agent.skills.services.artifacts.list() == []


def test_browser_failure_keeps_the_file_and_reports_failure(visual_agent, monkeypatch):
    def fail(path):
        raise ServiceError('unavailable', 'no browser')
    monkeypatch.setattr(host_actions, 'open_visualization', fail)
    result = call(visual_agent)['data']['result']
    assert not result['opened'] and result['open_error'] == 'no browser'
    assert Path(result['artifact']['path']).exists()


def test_save_without_open_and_readonly_denial(visual_agent, monkeypatch):
    opened = []
    monkeypatch.setattr(host_actions, 'open_visualization', opened.append)
    result = call(visual_agent, page=example(), open_browser=False)['data']['result']
    assert result['valid'] and not result['opened'] and opened == []
    visual_agent._state.read_only_goal = True
    assert not call(visual_agent)['ok']
    assert call(visual_agent, 'validate')['ok']
    assert len(visual_agent.skills.services.artifacts.list()) == 1


def test_open_permission_cannot_be_bypassed(visual_agent, monkeypatch):
    skill = visual_agent.skills.attached['visualize']
    monkeypatch.setattr(type(skill), 'allows', lambda self, name: name != 'host.open')
    result = call(visual_agent)
    assert not result['ok'] and 'host.open' in result['error']
    assert visual_agent.skills.services.artifacts.list() == []


def test_other_skills_cannot_call_visualize_core(visual_agent):
    from types import SimpleNamespace
    forged = SimpleNamespace(skill=SimpleNamespace(manifest=SimpleNamespace(core_group='host')))
    with pytest.raises(ServiceError, match='only through'):
        CoreServices.handle(visual_agent.skills.services, forged, 'visualize.show', {'page': example()})


@pytest.mark.parametrize('platform', ['linux', 'darwin', 'win32'])
def test_default_browser_receives_encoded_file_uri_on_each_platform(tmp_path, monkeypatch, platform):
    opened = []
    monkeypatch.setattr(host_actions.sys, 'platform', platform)
    monkeypatch.setenv('DISPLAY', ':test')
    monkeypatch.setattr(host_actions.webbrowser, 'open', lambda uri, new: opened.append((uri, new)) or True)
    path = tmp_path / 'diagram with spaces.html'
    path.write_text('page')
    host_actions.open_visualization(path)
    assert opened == [(path.as_uri(), 2)] and '%20' in opened[0][0]


def test_headless_linux_does_not_launch_a_blocking_text_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(host_actions.sys, 'platform', 'linux')
    monkeypatch.delenv('DISPLAY', raising=False)
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    with pytest.raises(ServiceError, match='no graphical desktop'):
        host_actions.open_visualization(tmp_path / 'page.html')


def test_reference_examples_are_valid():
    reference = (ROOT / 'references' / 'format.md').read_text()
    examples = [json.loads(block) for block in re.findall(r'```json\n(.*?)```', reference, re.S)]
    assert not validate_page(examples[0])
    assert not validate_page({'title': '3D', 'sections': [examples[1]]})


def test_bundled_visualization_has_no_dependencies_on_all_desktop_platforms():
    manifest = load_manifest(ROOT)
    assert manifest.core_group == 'visualize'
    assert set(manifest.platforms) == {'linux', 'darwin', 'win32'}
    assert not manifest.python_deps
    assert package_digest(ROOT).startswith('sha256:')


def test_frontend_geometry_controls_and_exports():
    import shutil
    import subprocess

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the offline JavaScript tests')
    result = subprocess.run([node, '--test', str(Path(__file__).with_name('visualize_frontend.test.cjs'))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_labels_fit_nodes_and_large_diagrams_keep_their_native_width():
    page = example()
    page['sections'][0]['nodes'][0]['label'] = ('longwordone ' * 8).strip()
    page['sections'][0]['nodes'][1]['column'] = 40
    output = render_page(page)
    assert 'data-width="13610"' in output
    node = re.search(r'<g class="node .*?</g>', output, re.S)[0]
    assert len(re.findall('<text ', node)) <= 4


def test_text_and_chart_palette_contrast_in_both_themes():
    css = (ROOT / 'page.css').read_text()

    def luminance(value):
        channels = [int(value[i:i+2], 16)/255 for i in (1, 3, 5)]
        linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in channels]
        return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))

    for block in css.split('\n')[:2]:
        colors = dict(re.findall(r'--([\w-]+):(#[0-9a-f]{6})', block))
        for name, threshold in [('text', 4.5), ('muted', 4.5), ('accent', 3), ('line', 3),
                                *[(name, 3) for name in ('blue', 'green', 'orange', 'purple', 'cyan', 'pink')]]:
            for background in ('bg', 'surface'):
                low, high = sorted([luminance(colors[name]), luminance(colors[background])])
                assert (high+.05)/(low+.05) >= threshold, (name, background, (high+.05)/(low+.05))
