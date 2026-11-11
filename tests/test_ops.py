

def test_upstream_climbs_to_the_roots_and_names_an_orphan(tmp_path, monkeypatch):
    # general → specific → leaf: the leaf's upstream is the chain root-first; a procedure nothing links is an orphan.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from playbook import ops
    ops.create_procedure("compose-piece", title="Compose a piece", description="general", tags=["music"])
    ops.create_procedure("pedal-per-harmony", title="Pedal per harmony", description="specific", tags=["pedal"])
    ops.create_procedure("lone-tune", title="Lone tune", description="nobody links", tags=["x"])
    ops.add_step("compose-piece", title="Pedal it", do="check the pedal against every harmony", procedure="pedal-per-harmony")
    up = ops.upstream("pedal-per-harmony")
    assert not up["orphan"] and up["roots"] == ["compose-piece"] and up["depth"] == 1
    assert [n["id"] for n in up["chains"][0]] == ["compose-piece", "pedal-per-harmony"]
    assert ops.upstream("lone-tune")["orphan"] is True
