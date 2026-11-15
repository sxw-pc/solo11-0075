from __future__ import annotations
from page_ledger.audit.engine import AuditEngine
from page_ledger.audit.model import SchemaObject
from page_ledger.audit.schema_check import audit_schema
from page_ledger.db.btree import RootWalk, TreePage
from page_ledger.db.header import parse_header
from page_ledger.db.pages import parse_page
class FakeReader:
    actual_page_count = 10
class FakeWalker:
    def __init__(self, root, tree_page=None, complete=True):
        self.reader = FakeReader()
        self.root = root
        self.tree_page = tree_page
        self.complete = complete
        self.pages = {root: tree_page} if tree_page else {}
        self.roots = []
    def walk_root(self, root_page, role, expected_kind, collect_schema=False):
        pages = [root_page] if self.tree_page else []
        return RootWalk(root_page, role, expected_kind, pages=pages, cell_count=1, max_depth=0, complete=self.complete)
def _clean_objects(path):
    report = AuditEngine().run(str(path))
    return report, [SchemaObject(**{key: item[key] for key in ("object_type", "name", "table_name", "root_page")}) for item in report["schema"]["objects"]]
def test_clean_schema_reconciliation(fixture_paths):
    report, _ = _clean_objects(fixture_paths["clean"])
    schema = report["schema"]
    assert schema["sidecar"]["available"] is True
    assert schema["sidecar"]["page_count"] == report["header"]["declared_pages"]
    assert schema["parsed_root_pages"] == schema["walked_root_pages"]
    assert schema["object_counts"]["table"] == 3
    assert schema["object_counts"]["index"] == 3
    assert all(item["traversed"] for item in schema["objects"])
def test_sidecar_unavailable(fixture_paths):
    report = AuditEngine().run(str(fixture_paths["broken_header"]))
    assert report["schema"]["sidecar"]["available"] is False
    assert "SCHEMA_SIDECAR_UNAVAILABLE" in {item["code"] for item in report["findings"]}
def test_missing_root(fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    header = parse_header(data, len(data))
    walker = FakeWalker(99)
    obj = SchemaObject("table", "missing_table", "missing_table", 99)
    problems, _ = audit_schema(header, walker, [obj], str(fixture_paths["clean"]))
    assert "SCHEMA_ROOT_MISSING" in {item.code for item in problems}
def test_root_type_mismatch(fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    header = parse_header(data, len(data))
    parse = parse_page(data, 3, header.page_size, header.usable_size, header.actual_page_count)
    tree = TreePage(parse, None, 0, "table")
    walker = FakeWalker(3, tree)
    obj = SchemaObject("table", "wrong_type", "wrong_type", 3)
    problems, _ = audit_schema(header, walker, [obj], str(fixture_paths["clean"]))
    codes = {item.code for item in problems}
    assert "SCHEMA_ROOT_TYPE_MISMATCH" in codes
def test_traversal_failed(fixture_paths):
    data = fixture_paths["clean"].read_bytes()
    header = parse_header(data, len(data))
    parse = parse_page(data, 3, header.page_size, header.usable_size, header.actual_page_count)
    walker = FakeWalker(3, TreePage(parse, None, 0, "index"), complete=False)
    obj = SchemaObject("index", "partial_index", "partial_index", 3)
    problems, _ = audit_schema(header, walker, [obj], str(fixture_paths["clean"]))
    assert "SCHEMA_TRAVERSAL_FAILED" in {item.code for item in problems}
