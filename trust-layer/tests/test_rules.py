"""python -m unittest discover tests   (vanuit de map trust-layer)"""
import unittest
from datetime import date

from trust import engine, specialist, test_agent
from trust.kb import KnowledgeBase

TODAY = date(2026, 9, 30)


class TestAgentSuite(unittest.TestCase):
    """De volledige Test Agent moet 100% halen op de demo-dataset."""

    @classmethod
    def setUpClass(cls):
        cls.report = test_agent.run(KnowledgeBase(persist=False), use_judge=False, save=False)

    def test_all_golden_questions_pass(self):
        failed = [(c["id"], [x for x in c["checks"] if not x["pass"]]) for c in self.report["cases"] if not c["pass"]]
        self.assertEqual(failed, [])

    def test_all_hard_rules_pass(self):
        failed = {k: v["failed"] for k, v in self.report["rules"].items() if not v["pass"]}
        self.assertEqual(failed, {})


class Extraction(unittest.TestCase):
    def test_normalizes_dutch_amounts(self):
        self.assertEqual(specialist.normalize_value("meal_voucher.amount", "De maaltijdcheque bedraagt 5,50 euro."),
                         (5.5, "€5.50 per day"))

    def test_date_in_sentence_is_not_an_amount(self):
        value, _ = specialist.normalize_value("meal_voucher.amount", "Vanaf 1 januari 2027 bedraagt de maaltijdcheque 8 euro.")
        self.assertEqual(value, 8.0)

    def test_question_is_not_a_claim(self):
        doc = {"id": "x", "company_id": "c", "valid_from": "2026-01-01", "country": "BE",
               "text": "Klopt het dat de maaltijdcheque al 8 euro is?"}
        kb = KnowledgeBase(persist=False)
        claims, noise = specialist.extract_rules(doc, list(kb.topics.values()))
        self.assertEqual(claims, [])
        self.assertEqual(noise[0]["reason"], "question, not a statement")


class HardRules(unittest.TestCase):
    def setUp(self):
        self.kb = KnowledgeBase(persist=False)

    def test_future_version_is_badge_not_answer(self):
        r = engine.resolve(self.kb, "brouwer", "meal_voucher.amount", TODAY, today=TODAY)
        self.assertEqual(r["answer"]["claim"]["value"], 7.0)
        self.assertEqual([u["claim"]["value"] for u in r["upcoming"]], [8.0])

    def test_swipes_cannot_lift_a_conflicting_source(self):
        r = engine.resolve(self.kb, "brouwer", "meal_voucher.amount", TODAY, today=TODAY)
        loser = r["current"][1]["claim"]["id"]
        flood = [{"claim_id": loser, "direction": "right", "role": "hr", "date": TODAY.isoformat()}] * 5000
        with self.kb.vote_overlay(flood):
            r2 = engine.resolve(self.kb, "brouwer", "meal_voucher.amount", TODAY, today=TODAY)
        self.assertEqual(r2["answer"]["claim"]["value"], 7.0)
        self.assertGreater(r2["current"][1]["human"]["score"], 0.99)

    def test_swipe_across_tenants_is_refused(self):
        kb = KnowledgeBase(persist=False)
        with self.assertRaises(LookupError):
            kb.add_swipe("u1", "employee", ["verhoeven"], "b-maaltijd-2026#meal_voucher.amount#0", "right")

    def test_one_vote_per_user_per_claim(self):
        kb = KnowledgeBase(persist=False)
        cid = "b-maaltijd-2026#meal_voucher.amount#0"
        before = engine.human(kb, kb.claims[cid], TODAY)["votes"]
        for _ in range(5):
            kb.add_swipe("u1", "employee", ["brouwer"], cid, "right")
        _, previous = kb.add_swipe("u1", "employee", ["brouwer"], cid, "left", "fout")
        self.assertIsNotNone(previous)
        self.assertEqual(engine.human(kb, kb.claims[cid], TODAY)["votes"], before + 1)

    def test_left_swipe_requires_reason(self):
        with self.assertRaises(ValueError):
            self.kb.add_swipe("u1", "employee", ["brouwer"], "b-maaltijd-2026#meal_voucher.amount#0", "left")

    def test_lower_authority_cannot_supersede_in_chain(self):
        kb = KnowledgeBase(persist=False)
        doc = {"id": "u-handboek", "company_id": "brouwer", "title": "Handboek", "source_type": "handbook",
               "chain_id": "brouwer-maaltijd", "version": "x", "published": "2026-06-01", "valid_from": "2026-06-01",
               "status": "published", "country": "BE", "owner": None, "validated": None,
               "text": "De maaltijdcheque bedraagt 9 euro per dag."}
        claims, _, _ = specialist.extract(doc, list(kb.topics.values()))
        kb.add_document(doc, claims)
        r = engine.resolve(kb, "brouwer", "meal_voucher.amount", TODAY, today=TODAY)
        self.assertEqual(r["answer"]["doc"]["id"], "b-maaltijd-2026")  # het beleid blijft gelden
        self.assertTrue(r["conflict"])                                 # het handboek wordt een conflict


if __name__ == "__main__":
    unittest.main()
