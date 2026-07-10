#!/usr/bin/env python3
"""
Tests for LinkedIn InMail / Conversation ad copy
(docs/linkedin-inmail-copy-plan.md). Exercises the copy gate on the new row
shapes + the flag-gated template injection. No live LLM. Run:
`python tests_linkedin_copy.py`.
"""
import sys
from datetime import date

import generate_assets as G

RUN_DATE = date(2026, 7, 10)
RUN_ID = "li-copy-test"


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"PASS: {msg}")


def _li(fmt, **extra):
    base = {"run_date": "2026-07-10", "run_id": RUN_ID,
            "campaign_name": "IK-IN_LinkedIn_EdgeUp_Conversation_EM_JF",
            "segment": "LinkedIn", "platform": "linkedin", "linkedin_format": fmt,
            "source_signal": "pmis_recommendations: EM InMail angle", "status": "proposed"}
    base.update(extra)
    return base


def test_gate_accepts_linkedin_message_and_conversation():
    message = _li("message", asset_id="c_li_msg01", cta="Register",
                  subject="Your team is upleveling in Agentic AI. Are you ready to lead them?",
                  body="Hi [Name],\n\n<long body with • bullets and CTA>\nBest,\n[Your Name]")
    convo = _li("conversation", asset_id="c_li_convo01", cta="Learn More",
                body="Step 1 (Initial):\nHi [First Name], ...\n* Yes, tell me more\n* No\n\nStep 2 (if Yes): ...")
    valid, dropped, reasons = G.gate_copy_rows([message, convo])
    check(dropped == 0 and len(valid) == 2, f"gate accepts message + conversation ({reasons})")
    by_fmt = {r.linkedin_format: r for r in valid}
    check(by_fmt["message"].subject and by_fmt["message"].headline is None,
          "message row keeps subject, no headline")
    check(by_fmt["conversation"].body.startswith("Step 1") and by_fmt["conversation"].subject is None,
          "conversation row carries the full flow body, no subject")


def test_gate_drops_message_without_subject():
    bad = _li("message", asset_id="c_li_msg02", body="Hi [Name] ...")  # no subject
    valid, dropped, reasons = G.gate_copy_rows([bad])
    check(dropped == 1 and not valid, "message row without subject is dropped by the gate")


def test_gate_still_accepts_legacy_sponsored_content():
    legacy = {"run_date": "2026-07-10", "run_id": RUN_ID, "campaign_name": "IK-IN_LinkedIn_Legacy",
              "segment": "LinkedIn", "platform": "linkedin", "asset_id": "c_li01",
              "headline": "Crack MAANG+ interviews", "primary_text": "Live cohort, FAANG mentors.",
              "cta": "Learn More", "source_signal": "executive_summary rank 1", "status": "proposed"}
    valid, dropped, reasons = G.gate_copy_rows([legacy])
    check(dropped == 0 and len(valid) == 1 and valid[0].linkedin_format is None,
          "legacy Sponsored Content linkedin row still validates (flag-off parity)")


def test_template_block_flag_gated():
    orig = G.LINKEDIN_INMAIL_ENABLED
    try:
        G.LINKEDIN_INMAIL_ENABLED = False
        check(G._linkedin_template_block() == "", "flag off -> no template block (prompt unchanged)")
        G.LINKEDIN_INMAIL_ENABLED = True
        block = G._linkedin_template_block()
        check("LinkedIn creative templates (FOLLOW EXACTLY" in block
              and "Subject:" in block and "Step 1" in block,
              "flag on -> injects both verbatim templates (message subject + conversation steps)")
    finally:
        G.LINKEDIN_INMAIL_ENABLED = orig


if __name__ == "__main__":
    test_gate_accepts_linkedin_message_and_conversation()
    test_gate_drops_message_without_subject()
    test_gate_still_accepts_legacy_sponsored_content()
    test_template_block_flag_gated()
    print("\nAll tests_linkedin_copy assertions passed.")
