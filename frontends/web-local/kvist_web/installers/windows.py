"""Windows Task Scheduler installer using an owned XML definition."""
from __future__ import annotations

import os
import subprocess
import xml.etree.ElementTree as ET

from .base import AutostartInstaller, InstallResult


class WindowsInstaller(AutostartInstaller):
    task_name = "Kvist Web"

    @property
    def path(self):
        base = os.environ.get("LOCALAPPDATA") or str(self.home / "AppData" / "Local")
        return __import__("pathlib").Path(base) / "Kvist" / "web" / "kvist-web-task.xml"

    def install(self) -> InstallResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("Task", {"version": "1.4", "xmlns": "http://schemas.microsoft.com/windows/2004/02/mit/task"})
        ET.SubElement(root, "RegistrationInfo").append(ET.Element("Description"))
        root.find("RegistrationInfo/Description").text = "Kvist local web controller"
        triggers = ET.SubElement(root, "Triggers")
        ET.SubElement(triggers, "LogonTrigger").append(ET.Element("Enabled"))
        root.find("Triggers/LogonTrigger/Enabled").text = "true"
        principals = ET.SubElement(root, "Principals")
        principal = ET.SubElement(principals, "Principal", {"id": "Author"})
        ET.SubElement(principal, "LogonType").text = "InteractiveToken"
        ET.SubElement(principal, "RunLevel").text = "LeastPrivilege"
        settings = ET.SubElement(root, "Settings")
        ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
        ET.SubElement(settings, "DisallowStartIfOnBatteries").text = "false"
        ET.SubElement(settings, "StopIfGoingOnBatteries").text = "false"
        ET.SubElement(settings, "StartWhenAvailable").text = "true"
        ET.SubElement(settings, "ExecutionTimeLimit").text = "PT0S"
        ET.SubElement(settings, "RestartOnFailure").extend(
            [ET.Element("Interval"), ET.Element("Count")]
        )
        settings.find("RestartOnFailure/Interval").text = "PT1M"
        settings.find("RestartOnFailure/Count").text = "3"
        actions = ET.SubElement(root, "Actions", {"Context": "Author"})
        execution = ET.SubElement(actions, "Exec")
        launch = self.service_command()
        ET.SubElement(execution, "Command").text = launch[0]
        arguments = launch[1:]
        ET.SubElement(execution, "Arguments").text = subprocess.list2cmdline(arguments)
        ET.SubElement(execution, "WorkingDirectory").text = str(self.home)
        ET.ElementTree(root).write(self.path, encoding="utf-16", xml_declaration=True)
        self.runner(
            ["schtasks.exe", "/Create", "/TN", self.task_name, "/XML", str(self.path), "/F"]
        )
        return InstallResult("installed", str(self.path), "Installed the Kvist logon task.")

    def uninstall(self) -> InstallResult:
        if self.path.exists():
            try:
                self.runner(["schtasks.exe", "/Delete", "/TN", self.task_name, "/F"])
            finally:
                self.path.unlink(missing_ok=True)
        return InstallResult("uninstalled", str(self.path), "Removed the Kvist logon task.")
