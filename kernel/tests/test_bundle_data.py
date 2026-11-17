# -*- coding: utf-8 -*-
from __future__ import annotations

def test_kernel_package_data_matches_bundle():
    import ast, pathlib, tomllib
    root=pathlib.Path(__file__).resolve().parents[2]; config=tomllib.loads((root/'pyproject.toml').read_text()); pd=config['tool']['setuptools']['package-data']; expected=set()
    for pkg, patterns in pd.items():
     if not pkg.startswith('kernel.'): continue
     d=root.joinpath(*pkg.split('.'))
     for pat in patterns:
      expected.update(p.relative_to(root).as_posix() for p in d.glob(pat) if p.is_file() and p.name!='__init__.py')
    spec=ast.parse((root/'kvist.spec').read_text()); call=next(n for n in ast.walk(spec) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='Analysis'); datas=next(k.value for k in call.keywords if k.arg=='datas'); actual=set()
    for n in datas.elts:
     if not isinstance(n,ast.Tuple): continue
     try: src,dst=ast.literal_eval(n)
     except (ValueError,TypeError): continue
     if not src.startswith('kernel/'): continue
     p=root/src
     if p.is_dir(): actual.update(x.relative_to(root).as_posix() for x in p.rglob('*') if x.is_file() and x.suffix not in ('.pyc',) and x.name!='__init__.py' and '__pycache__' not in x.parts)
     elif p.is_file(): actual.add(src)
    assert expected - actual == set(), sorted(expected - actual)
    assert {p for p in actual - expected if not p.endswith('.py')} == set(), sorted(p for p in actual - expected if not p.endswith('.py'))
