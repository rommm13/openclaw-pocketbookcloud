import json
import unittest
from pathlib import Path

from test_pocketbook import pb
import pocketbook_lib.api as api_mod

FIXTURES = Path(__file__).resolve().parent / "fixtures"

def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

class ContractFixtureTests(unittest.TestCase):
    def test_provider_fixture_parses(self):
        providers=api_mod._parse_providers_payload(fixture("providers.json"))
        self.assertEqual(providers[0]["alias"],"fixture-provider")

    def test_login_token_fixture_parses(self):
        access,refresh,expires=api_mod._parse_token_payload(fixture("login_token.json"),require_refresh=True)
        self.assertEqual(access,"fixture-access-token")
        self.assertEqual(refresh,"fixture-refresh-token")
        self.assertEqual(expires,7200)

    def test_books_fixture_parses_and_ignores_future_fields(self):
        total,items=api_mod._parse_books_page(fixture("books_page.json"),require_total=True)
        self.assertEqual(total,1); self.assertEqual(items[0]["fast_hash"],"fixture-fast-hash")

    def test_notes_fixture_parses_and_ignores_future_fields(self):
        total,items=api_mod._parse_notes_payload(fixture("notes_page.json"))
        self.assertEqual(total,1); self.assertEqual(items[0]["uuid"],"fixture-note")

    def test_provider_missing_alias_fails_closed(self):
        data=fixture("providers.json"); del data["providers"][0]["alias"]
        with self.assertRaises(pb.PocketBookError): api_mod._parse_providers_payload(data)

    def test_token_wrong_expiry_type_fails_closed(self):
        data=fixture("login_token.json"); data["expires_in"]={"bad":True}
        with self.assertRaises(pb.PocketBookError): api_mod._parse_token_payload(data,require_refresh=True)

    def test_books_missing_total_fails_when_required(self):
        data=fixture("books_page.json"); del data["total"]
        with self.assertRaises(pb.PocketBookError): api_mod._parse_books_page(data,require_total=True)

    def test_books_page_can_omit_total_after_head_request(self):
        data=fixture("books_page.json"); del data["total"]
        total,items=api_mod._parse_books_page(data,require_total=False)
        self.assertIsNone(total); self.assertEqual(len(items),1)

if __name__ == "__main__": unittest.main(verbosity=2)
