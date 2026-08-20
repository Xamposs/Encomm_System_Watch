"""VM collector tests (Phase 21).

Fixture-driven: no hypervisor, Hyper-V CIM, VMware processes, VirtualBox
list output, ambiguous hypervisor process, and the hard rule that no
control command is ever executed.
"""
import types

import pytest

from app.collectors import vm as vm_mod
from app.collectors.vm import VmCollector, VmInfo


def _ps_run(results):
    calls = []

    def runner(args, timeout=10.0, **kwargs):
        calls.append(list(args))
        for script_frag, out in results:
            if script_frag in " ".join(args):
                return types.SimpleNamespace(returncode=0,
                                             stdout=out.encode("utf-8", errors="replace"),
                                             stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")
    return runner, calls


@pytest.fixture
def no_hypervisors(monkeypatch):
    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b""),
        CREATE_NO_WINDOW=0,
    ))
    monkeypatch.setattr(vm_mod, "VBOX_PATHS", [r"C:\none\VBoxManage.exe"])
    monkeypatch.setattr(vm_mod, "VMWARE_EXE_DIRS", [])
    monkeypatch.setattr(vm_mod, "VMRUN_PATHS", [])
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [])
    return VmCollector()


def _proc(pid, name, cmdline=None):
    return {"pid": pid, "name": name, "exe": r"C:\bin\\" + name, "cmdline": cmdline or [name]}


def test_no_hypervisor(no_hypervisors):
    st = no_hypervisors.collect()
    assert st.vms == []
    assert st.providers["HYPER_V"]["installed"] is True  # PS answered (empty)
    assert st.providers["HYPER_V"]["count"] == 0
    assert st.providers["VMWARE"]["installed"] is False
    assert st.providers["VIRTUALBOX"]["installed"] is False


def test_hyperv_fixture(monkeypatch):
    hv_json = (
        '[{"Name":"Win11-Lab","Id":"11111111-2222-3333-4444-555555555555",'
        '"State":"Running","Generation":2,"MemoryAssigned":4294967296,'
        '"Uptime":"1.02:03:04","ProcessorCount":4,'
        '"NetAdapters":["Default Switch"]}]'
    )
    vmwp_json = (
        '[{"Pid":9999,"Cmd":"C:\\\\Windows\\\\System32\\\\vmwp.exe -G '
        '11111111-2222-3333-4444-555555555555"}]'
    )
    runner, calls = _ps_run([("Get-VM", hv_json + "\n---VMWP---\n" + vmwp_json)])
    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=runner, CREATE_NO_WINDOW=0,
    ))
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [])
    st = VmCollector().collect()
    assert st.providers["HYPER_V"]["installed"] is True
    assert st.providers["HYPER_V"]["count"] == 1
    v = st.vms[0]
    assert v.provider == "HYPER_V"
    assert v.name == "Win11-Lab"
    assert v.state == "RUNNING"
    assert v.confidence == "CONFIRMED"
    assert v.host_pid == 9999  # vmwp mapping via GUID evidence
    assert v.metadata["generation"] == 2
    assert v.metadata["memory_mb"] == 4096
    assert v.metadata["uptime_s"] == 93784
    assert v.metadata["network_adapters"] == ["Default Switch"]
    assert "Get-VM" in " ".join(calls[0])


def test_hyperv_absent_module(monkeypatch):
    runner, _ = _ps_run([("Get-VM", "")])
    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout=b"Get-VM : The term 'Get-VM' is not recognized",
            stderr=b""),
        CREATE_NO_WINDOW=0,
    ))
    st = VmCollector().collect()
    assert st.providers["HYPER_V"]["installed"] is False
    assert "not recognized" in st.providers["HYPER_V"].get("error", "")


def test_vmware_fixture(monkeypatch):
    monkeypatch.setattr(vm_mod, "VMWARE_EXE_DIRS", [types.SimpleNamespace(is_dir=lambda: True)])
    monkeypatch.setattr(vm_mod, "VMRUN_PATHS", [])
    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b""),
        CREATE_NO_WINDOW=0,
    ))
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [
        _proc(7000, "vmware-vmx.exe",
              ["vmware-vmx.exe", r"C:\VMs\WinDev\WinDev.vmx", "-x"]),
    ])
    st = VmCollector().collect()
    v = st.vms[0]
    assert v.provider == "VMWARE"
    assert v.name == "WinDev"
    assert v.state == "RUNNING"
    assert v.confidence == "HIGH"
    assert v.host_pid == 7000
    assert v.metadata["vmx_file"] == "WinDev.vmx"  # path redacted to file name


def test_ambiguous_hypervisor_process(monkeypatch):
    """vmware-vmx.exe without a readable .vmx path -> VIRTUALIZATION PROCESS,
    never a made-up VM name."""
    monkeypatch.setattr(vm_mod, "VMWARE_EXE_DIRS", [types.SimpleNamespace(is_dir=lambda: True)])
    monkeypatch.setattr(vm_mod, "VMRUN_PATHS", [])
    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b""),
        CREATE_NO_WINDOW=0,
    ))
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [
        _proc(7001, "vmware-vmx.exe", ["vmware-vmx.exe", "-x", "--strict"]),
    ])
    st = VmCollector().collect()
    assert len(st.vms) == 1
    v = st.vms[0]
    assert v.name is None
    assert v.provider == "VMWARE"
    assert v.confidence == "MEDIUM"
    assert v.identity == "VIRTUALIZATION PROCESS"


def test_virtualbox_fixture(monkeypatch):
    import pathlib as _pl

    class FakePath:
        def __init__(self, p):
            self._p = str(p)

        def is_file(self):
            return True

        @property
        def stem(self):
            return _pl.Path(self._p).stem

        @property
        def name(self):
            return _pl.Path(self._p).name

        def __str__(self):
            return self._p

    monkeypatch.setattr(vm_mod, "Path", FakePath)
    monkeypatch.setattr(vm_mod, "VBOX_PATHS", [r"C:\Oracle\VirtualBox\VBoxManage.exe"])
    calls = []

    def runner(args, timeout=10.0, **kwargs):
        calls.append(list(args))
        if args and args[0].endswith("VBoxManage.exe") and args[1] == "list":
            return types.SimpleNamespace(returncode=0,
                                         stdout=b'"TestVM" {aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}\r\n',
                                         stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=runner, CREATE_NO_WINDOW=0,
    ))
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [
        _proc(8000, "VBoxHeadless.exe", ["VBoxHeadless.exe", "--comment", "TestVM"]),
    ])
    st = VmCollector().collect()
    assert st.providers["VIRTUALBOX"]["installed"] is True
    assert st.providers["VIRTUALBOX"]["running"] == 1
    v = st.vms[0]
    assert v.provider == "VIRTUALBOX"
    assert v.name == "TestVM"
    assert v.state == "RUNNING"
    assert v.confidence == "CONFIRMED"
    assert v.host_pid == 8000  # headless process attached as host evidence
    # every VBoxManage call is a read-only LIST (the powershell probe is not
    # a VBoxManage call)
    for call in calls:
        if call and call[0].endswith("VBoxManage.exe"):
            assert call[1] == "list"


def test_no_control_commands_executed(no_hypervisors, monkeypatch):
    """The collector must never invoke any VM control command."""
    forbidden = ("startvm", "controlvm", "modifyvm", "start", "stop",
                 "pause", "resume", "reset", "snapshot", "Start-VM",
                 "Stop-VM", "Restart-VM")
    calls = []
    monkeypatch.setattr(VmCollector, "_procs", lambda self: [
        _proc(7002, "vmware-vmx.exe", ["vmware-vmx.exe", r"C:\VMs\X\X.vmx"]),
        _proc(8001, "VBoxHeadless.exe", ["VBoxHeadless.exe"]),
    ])
    # force a hypervisor path so more commands would run if they existed
    monkeypatch.setattr(vm_mod, "VBOX_PATHS", [r"C:\Oracle\VirtualBox\VBoxManage.exe"])
    monkeypatch.setattr(vm_mod, "VMWARE_EXE_DIRS", [types.SimpleNamespace(is_dir=lambda: True)])
    monkeypatch.setattr(vm_mod, "VMRUN_PATHS", [r"C:\VMware\vmrun.exe"])

    # make the vmrun path "exist" for the read-only list probe
    import pathlib as _pl

    class FakePath:
        def __init__(self, p):
            self._p = str(p)

        def is_file(self):
            return True

        @property
        def stem(self):
            return _pl.Path(self._p).stem

        @property
        def name(self):
            return _pl.Path(self._p).name

        def __str__(self):
            return self._p

    monkeypatch.setattr(vm_mod, "Path", FakePath)

    def runner(args, timeout=10.0, **kwargs):
        calls.append(" ".join(args))
        joined = " ".join(args).lower()
        if "vmrun" in joined:
            return types.SimpleNamespace(returncode=0,
                                         stdout=b"Total running VMs: 1\r\n", stderr=b"")
        if "vboxmanage" in joined:
            return types.SimpleNamespace(returncode=0,
                                         stdout=b'"X" {aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}\r\n',
                                         stderr=b"")
        return types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")

    monkeypatch.setattr(vm_mod, "subprocess", types.SimpleNamespace(
        run=runner, CREATE_NO_WINDOW=0,
    ))
    no_hypervisors.collect()
    joined = " ".join(calls).lower()
    for tok in forbidden:
        assert tok not in joined, f"forbidden control token executed: {tok}"
    assert "vmrun.exe list" in joined  # read-only list is the only vmrun call
