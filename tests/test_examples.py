"""Worked-example smoke tests: the shipped
example must stay runnable and honest — its corpus exists,
its recorded build output parses as triple records, and
every evidence quote is a verbatim substring of the
document it cites (the inspect view is only worth what
its evidence is worth).
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


class TestWorkedExample(unittest.TestCase):
    def test_corpus_documents_exist(self):
        docs = sorted((EXAMPLES / "corpus").glob("*.txt"))
        self.assertGreaterEqual(len(docs), 1)
        for doc in docs:
            self.assertTrue(doc.read_text().strip())

    def test_recorded_triples_are_triple_shaped(self):
        from pipeline.inspect import is_triple_record

        lines = (EXAMPLES / "alder-finch" / "triples.jsonl") \
            .read_text().splitlines()
        records = [json.loads(ln) for ln in lines if ln.strip()]
        self.assertGreaterEqual(len(records), 1)
        for rec in records:
            self.assertTrue(is_triple_record(rec), rec)

    def test_demo_build_records_are_triple_shaped(self):
        # The shipped demo build (examples/triples.jsonl):
        # a real full-text build's frozen records — many,
        # all triple-shaped, all carrying evidence quotes
        # and chapter doc ids.
        from pipeline.inspect import is_triple_record

        lines = (EXAMPLES / "triples.jsonl").read_text() \
            .splitlines()
        records = [json.loads(ln) for ln in lines if ln.strip()]
        self.assertGreaterEqual(len(records), 500)
        for rec in records:
            self.assertTrue(is_triple_record(rec), rec)
            self.assertTrue(rec["quote"].strip(), rec)
            self.assertTrue(rec["doc_id"].startswith("ch-"),
                            rec)

    def test_evidence_quotes_are_verbatim(self):
        lines = (EXAMPLES / "alder-finch" / "triples.jsonl") \
            .read_text().splitlines()
        for ln in lines:
            if not ln.strip():
                continue
            rec = json.loads(ln)
            doc = EXAMPLES / "corpus" / f"{rec['doc_id']}.txt"
            self.assertTrue(doc.is_file(), doc)
            self.assertIn(rec["quote"], doc.read_text())

    def test_inspect_renders_first_record_with_evidence(self):
        from pipeline.inspect import (
            build_report,
            load_records,
            render_card,
            select,
        )

        path = EXAMPLES / "alder-finch" / "triples.jsonl"
        records = load_records(path)
        rec, position, total = select(records)
        card = render_card(build_report(rec, path, position,
                                        total))
        self.assertIn("Evidence", card)
        self.assertNotIn("no evidence span recorded", card)


if __name__ == "__main__":
    unittest.main()
