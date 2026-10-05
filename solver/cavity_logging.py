"""Resolve the nonlinear-constraint index from the actual input model."""
def cavity_constraint_id(root):
    record = root.find("./Output/logfile/rigid_connector_data[@name='cavity_state']")
    if record is None or not record.text:
        raise ValueError("Cavity logger missing from model")
    return int(record.text.strip())
