from packet_foundry_contracts import Packet


def comparison_inputs(packet: Packet) -> dict:
    """Comparison case identity uses source content, independent of prompt/model variants."""
    sources = []
    for material in packet.materials:
        item = material.model_dump(mode="json", exclude={"artifact"})
        if material.artifact:
            item["artifact"] = material.artifact.model_dump(exclude={"artifact_id", "filename"})
        sources.append(item)
    return {"materials": sources}
