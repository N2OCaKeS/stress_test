from allta import SystemCommands, LibvirtManager

class Snapshot:

    def create(vms: list, snapshot_name: str):
        LibvirtManager.Snapshot.create(vms=vms, snapshot_name=snapshot_name)
    def delete(vms: list, snapshot_name: str):
        LibvirtManager.Snapshot.delete(vms=vms, snapshot_name=snapshot_name)
    def revert(vms: list, snapshot_name: str):
        LibvirtManager.Snapshot.revert(vms=vms, snapshot_name=snapshot_name
                                       )
    def delete_all(vms):
        snapshot_list_command = f"virsh -c qemu:///system snapshot-list --domain {vms}"
        output = SystemCommands.check_output_command(snapshot_list_command)
        lines = output.split('\n')[2:]
        snapshots = [line.split()[0] for line in lines if line.strip()]
        for snapshot in snapshots:
            LibvirtManager.Snapshot.delete(vms=vms, snapshot_name=snapshot)