import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pocketbook.py"
spec = importlib.util.spec_from_file_location("pocketbook_under_test", MODULE_PATH)
pb = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pb
spec.loader.exec_module(pb)
import pocketbook_lib.operations as operations_mod
import pocketbook_lib.transport as transport_mod


def raw_book(title="Alpha", author="Author", fast="fh1", ident="1", status="", progress=0, name="alpha.epub"):
    return {
        "id": ident,
        "fast_hash": fast,
        "name": name,
        "format": "epub",
        "metadata": {"title": title, "authors": author, "isbn": ""},
        "read_status": status,
        "read_percent": progress,
        "isDrm": False,
        "isLcp": False,
        "link": "https://files.example.test/book.epub",
    }


def valid_session(email="user@example.com", alias="pocketbook", shop="1"):
    return {
        "schema": 1,
        "email": email,
        "provider_alias": alias,
        "provider_name": "PocketBook",
        "shop_id": shop,
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": int(time.time()) + 3600,
    }


class FakeClient:
    def __init__(self, books, session_path):
        self._books = list(books)
        self.deleted = []
        self.store = pb.SessionStore(Path(session_path))
        self.session = valid_session()
        self.store.save(self.session)
    def books(self):
        return list(self._books)
    def delete(self, book):
        fh = book.get("fast_hash")
        self.deleted.append(fh)
        self._books = [b for b in self._books if b.get("fast_hash") != fh]
        return {"ok": True, "deleted": True, "fastHash": fh}
    def upload(self, path):
        return {"ok": True, "name": Path(path).name}


class PocketBookTests(unittest.TestCase):
    def test_response_empty_success_is_empty_json(self):
        self.assertEqual(pb.Response(200, {}, b"", "https://cloud.pocketbook.digital/x").json(), {})

    def test_response_invalid_json_is_sanitized(self):
        with self.assertRaises(pb.PocketBookError) as cm:
            pb.Response(200, {}, b"not-json", "https://signed.example/secret?token=x").json()
        self.assertNotIn("signed.example", str(cm.exception))
        self.assertNotIn("token=x", str(cm.exception))

    def test_progress_fraction(self):
        self.assertEqual(pb.progress_percent({"read_percent": 0.76}), 76)

    def test_progress_percent(self):
        self.assertEqual(pb.progress_percent({"position": {"percent": 98}}), 98)

    def test_finished_status(self):
        self.assertEqual(pb.reading_status({"read_status": "read", "read_percent": 10}), "finished")

    def test_progress_can_mark_finished(self):
        self.assertEqual(pb.reading_status({"read_percent": 100}), "finished")

    def test_progress_can_mark_reading(self):
        self.assertEqual(pb.reading_status({"read_percent": 12}), "reading")

    def test_filename_strips_traversal(self):
        self.assertEqual(pb.sanitize_filename("../../book.epub"), "book.epub")

    def test_filename_unicode_preserved(self):
        self.assertEqual(pb.sanitize_filename("Книга №1.epub"), "Книга №1.epub")

    def test_supported_extensions_casefold(self):
        self.assertTrue(pb.supported_filename("BOOK.FB2.ZIP"))
        self.assertTrue(pb.supported_filename("x.EPUB"))
        self.assertFalse(pb.supported_filename("x.exe"))

    def test_exact_fast_hash_wins(self):
        a = raw_book(title="Same", fast="aaa", ident="1")
        b = raw_book(title="Same", fast="bbb", ident="2")
        self.assertIs(pb.resolve_book([a,b], "bbb"), b)

    def test_duplicate_exact_title_is_ambiguous(self):
        a = raw_book(title="Same", author="One", fast="aaa", ident="1")
        b = raw_book(title="Same", author="Two", fast="bbb", ident="2")
        with self.assertRaises(pb.AmbiguousBook) as cm:
            pb.resolve_book([a,b], "Same")
        self.assertEqual(len(cm.exception.candidates), 2)

    def test_not_found(self):
        with self.assertRaises(pb.NotFound):
            pb.resolve_book([raw_book()], "ZZZ definitely missing")

    def test_session_store_mode_0600(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"session.json"
            pb.SessionStore(path).save(valid_session())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_prepare_ambiguous_creates_no_pending(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"
            c=FakeClient([raw_book("Same","A","a","1"),raw_book("Same","B","b","2")], session)
            with self.assertRaises(pb.AmbiguousBook): pb.prepare_delete(c,"Same")
            self.assertFalse(session.with_name("delete-pending.json").exists())

    def test_prepare_creates_private_code_free_pending(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"
            c=FakeClient([raw_book()], session)
            r=pb.prepare_delete(c,"fh1")
            pending=session.with_name("delete-pending.json")
            self.assertTrue(r["prepared"])
            self.assertNotIn("confirmationCode", r)
            data=json.loads(pending.read_text())
            self.assertEqual(data["schema"],4)
            self.assertNotIn("code",data["request"])
            self.assertEqual(data["request"]["fast_hash"],"fh1")
            self.assertEqual(pending.stat().st_mode & 0o777, 0o600)

    def test_legacy_confirmation_code_is_inert(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pb.prepare_delete(c,"fh1")
            r=pb.confirm_delete(c,"0C4B56")
            self.assertFalse(r["ok"])
            self.assertTrue(r["ignored"])
            self.assertEqual(r["reason"],"legacy_confirmation_code_is_inert")
            self.assertEqual(c.deleted,[])
            self.assertEqual(len(c.books()),1)

    def test_compound_first_turn_is_not_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pb.prepare_delete(c,"fh1")
            r=pb.confirm_delete(c,"найди книгу и удали")
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"],"explicit_next_message_confirmation_required")
            self.assertEqual(c.deleted,[])

    def test_new_prepare_replaces_old_pending(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"
            a=raw_book("A","Author","aaa","1")
            b=raw_book("B","Author","bbb","2")
            c=FakeClient([a,b],session)
            pb.prepare_delete(c,"aaa")
            pb.prepare_delete(c,"bbb")
            data=json.loads(session.with_name("delete-pending.json").read_text())
            self.assertEqual(data["request"]["fast_hash"],"bbb")
            r=pb.confirm_delete(c,"Удали")
            self.assertTrue(r["deleted"])
            self.assertEqual(c.deleted,["bbb"])
            self.assertEqual([x["fast_hash"] for x in c.books()],["aaa"])

    def test_confirmation_is_one_shot(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pb.prepare_delete(c,"fh1")
            r=pb.confirm_delete(c,"подтверждаю")
            self.assertTrue(r["deleted"]); self.assertEqual(c.deleted,["fh1"])
            again=pb.confirm_delete(c,"да")
            self.assertFalse(again["ok"])
            self.assertEqual(again["reason"],"no_active_delete_request")

    def test_expired_confirmation_is_safe_control_flow(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pb.prepare_delete(c,"fh1"); pending=session.with_name("delete-pending.json")
            d=json.loads(pending.read_text()); d["request"]["expires_at"]=int(time.time())-1; pending.write_text(json.dumps(d)); os.chmod(pending,0o600)
            r=pb.confirm_delete(c,"удали")
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"],"delete_confirmation_expired")
            self.assertEqual(len(c.books()),1)
            self.assertFalse(pending.exists())

    def test_old_schema_pending_is_never_migrated_to_actionable(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pending=session.with_name("delete-pending.json")
            pending.write_text(json.dumps({"schema":2,"requests":{"ABCDEF":{"fast_hash":"fh1","id":"1","expires_at":int(time.time())+900}}})); os.chmod(pending,0o600)
            r=pb.confirm_delete(c,"удали")
            self.assertFalse(r["ok"])
            self.assertEqual(r["reason"],"no_active_delete_request")
            self.assertEqual(c.deleted,[])
            self.assertFalse(pending.exists())

    def test_cancel_delete(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; c=FakeClient([raw_book()],session)
            pb.prepare_delete(c,"fh1")
            r=pb.cancel_delete(c)
            self.assertTrue(r["cancelled"])
            self.assertFalse(session.with_name("delete-pending.json").exists())

    def test_download_many_ambiguity_has_zero_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            c=FakeClient([raw_book("Same","A","a","1"),raw_book("Same","B","b","2")],Path(td)/"s.json")
            out=Path(td)/"out"
            with self.assertRaises(pb.AmbiguousBook): pb.download_many(c,["Same"],out)
            self.assertFalse(out.exists())

    def test_package_preserves_inner_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/"book.epub"; src.write_bytes(b"epub-bytes")
            out=Path(td)/"delivery.zip"
            r=pb.package_files([src],out)
            import zipfile
            with zipfile.ZipFile(out) as z:
                self.assertEqual(z.namelist(),["book.epub"])
                self.assertEqual(z.read("book.epub"),b"epub-bytes")
            self.assertEqual(r["files"],1)

    def test_package_rejects_duplicate_archive_names(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/"a"; b=Path(td)/"b"; a.mkdir(); b.mkdir()
            (a/"same.epub").write_bytes(b"a"); (b/"same.epub").write_bytes(b"b")
            with self.assertRaises(pb.UnsafeOperation):
                pb.package_files([a/"same.epub",b/"same.epub"],Path(td)/"x.zip")

    def test_upload_many_continues_after_pocketbook_error(self):
        class C:
            def __init__(self): self.n=0
            def upload(self,p):
                self.n+=1
                if self.n==2: raise pb.PocketBookError("bad")
                return {"ok":True,"verified":True}
        with tempfile.TemporaryDirectory() as td:
            paths=[]
            for name in ("a.fb2","b.fb2","c.fb2"):
                q=Path(td)/name; q.write_bytes(b"<FictionBook/>"); paths.append(q)
            r=pb.upload_many(C(),paths)
        self.assertEqual(r["uploaded"],2); self.assertEqual(r["failed"],1); self.assertFalse(r["ok"])


    def _make_epub(self, path, payload=b"hello"):
        import zipfile
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", "<container/>")
            z.writestr("OEBPS/content.xhtml", payload)

    def test_preflight_raw_epub_is_direct_not_extracted(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"book.epub"; self._make_epub(path)
            r=pb.preflight_upload([path])
            self.assertEqual(r["books"],1)
            self.assertEqual(r["items"][0]["kind"],"epub")
            self.assertEqual(r["items"][0]["action"],"direct")

    def test_preflight_fb2_zip_is_native_direct(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"book.fb2.zip"
            with zipfile.ZipFile(path,"w") as z: z.writestr("book.fb2",b"<FictionBook/>")
            r=pb.preflight_upload([path])
            self.assertEqual(r["items"][0]["kind"],"fb2_zip")
            self.assertEqual(r["items"][0]["action"],"direct")

    def test_epub_zip_transport_extracts_inner_epub_only(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); inner=td/"inner.epub"; self._make_epub(inner,b"payload")
            outer=td/"book.epub.zip"
            with zipfile.ZipFile(outer,"w") as z:
                z.write(inner,arcname="folder/book.epub")
                z.writestr("README.txt.extra",b"ignore")
            plan=pb.preflight_upload([outer])
            self.assertEqual(plan["books"],1)
            self.assertEqual(plan["items"][0]["kind"],"transport_zip")
            out=td/"out"
            materialized=pb.materialize_upload_inputs([outer],out)
            self.assertEqual(materialized["books"],1)
            extracted=Path(materialized["paths"][0])
            self.assertEqual(extracted.name,"book.epub")
            self.assertEqual(extracted.read_bytes(),inner.read_bytes())

    def test_transport_zip_ignores_obvious_readme_txt(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bundle.zip"
            with zipfile.ZipFile(path,"w") as z:
                z.writestr("book.epub",b"ebook")
                z.writestr("README.txt",b"instructions")
            r=pb.preflight_upload([path])
            self.assertEqual(r["books"],1)
            self.assertEqual(r["items"][0]["ignoredEntries"],1)

    def test_transport_zip_multiple_books_counts_before_extract(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"batch.zip"
            with zipfile.ZipFile(path,"w") as z:
                z.writestr("a.fb2",b"a"*10)
                z.writestr("b.epub",b"b"*20)
                z.writestr("cover.jpg",b"jpg")
            r=pb.preflight_upload([path])
            self.assertEqual(r["books"],2)
            self.assertEqual(r["estimatedBookBytes"],30)
            self.assertEqual(r["items"][0]["ignoredEntries"],1)

    def test_transport_zip_traversal_is_rejected(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.zip"
            with zipfile.ZipFile(path,"w") as z: z.writestr("../../evil.epub",b"x")
            with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([path])

    def test_transport_zip_absolute_windows_path_is_rejected(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.zip"
            with zipfile.ZipFile(path,"w") as z: z.writestr("C:/evil.epub",b"x")
            with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([path])

    def test_transport_zip_symlink_is_rejected(self):
        import zipfile, stat
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.zip"
            info=zipfile.ZipInfo("book.epub")
            info.create_system=3
            info.external_attr=(stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(path,"w") as z: z.writestr(info,"target")
            with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([path])

    def test_transport_zip_duplicate_flattened_names_rejected(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"dupes.zip"
            with zipfile.ZipFile(path,"w") as z:
                z.writestr("one/book.epub",b"a")
                z.writestr("two/book.epub",b"b")
            with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([path])

    def test_transport_zip_suspicious_ratio_rejected(self):
        import zipfile
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bomb.zip"
            with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("book.fb2",b"0"*(11*1024*1024))
            with patch.dict(os.environ,{"POCKETBOOK_MAX_ARCHIVE_RATIO":"20"}):
                with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([path])

    def test_preflight_upload_large_batch_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            paths=[]
            for i in range(11):
                p=Path(td)/f"{i}.fb2"; p.write_bytes(b"x"); paths.append(p)
            r=pb.preflight_upload(paths)
            self.assertTrue(r["requiresConfirmation"])
            self.assertIn("book_count",r["confirmationReasons"])

    def test_preflight_download_reports_count_and_bytes(self):
        class C:
            def books(self):
                a=raw_book("A",fast="a",ident="1"); a["bytes"]=100
                b=raw_book("B",fast="b",ident="2"); b["bytes"]=200
                return [a,b]
        r=pb.preflight_download(C(),["A","B"])
        self.assertEqual(r["books"],2)
        self.assertEqual(r["knownBytes"],300)
        self.assertEqual(r["unknownSizeBooks"],0)

    def test_preflight_download_ambiguity_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            c=FakeClient([raw_book("Same","A","a","1"),raw_book("Same","B","b","2")],Path(td)/"s.json")
            with self.assertRaises(pb.AmbiguousBook): pb.preflight_download(c,["Same"])

    def test_strict_api_redirect_blocks_cross_origin(self):
        h=pb.StrictApiRedirect()
        req=pb.Request("https://cloud.pocketbook.digital/a",headers={"Authorization":"Bearer secret"})
        with self.assertRaises(pb.UnsafeOperation):
            h.redirect_request(req,None,302,"x",{},"https://example.com/steal")

    def test_public_https_rejects_http_before_dns(self):
        with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("http://example.com/x")

    def test_public_https_rejects_url_credentials_before_dns(self):
        with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://u:p@example.com/x")

    def test_safe_redirect_drops_authorization(self):
        with patch.object(transport_mod,"ensure_public_https",lambda url: None):
            req=pb.Request("https://cloud.pocketbook.digital/a",headers={"Authorization":"Bearer secret","X-Test":"x"})
            out=pb.SafeRedirect().redirect_request(req,None,302,"x",{},"https://cdn.example.com/file")
            headers={k.lower():v for k,v in out.header_items()}
            self.assertNotIn("authorization",headers)
            self.assertNotIn("x-test",headers)

    def test_fb2_xml_is_detected_and_canonicalized(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"book.xml"
            path.write_text("<?xml version='1.0'?><FictionBook xmlns='http://www.gribuser.ru/xml/fictionbook/2.0'><description/></FictionBook>",encoding="utf-8")
            self.assertTrue(pb.is_fb2_xml(path))
            item=pb.inspect_upload_input(path)
            self.assertEqual(item["kind"],"fb2_xml")
            self.assertEqual(item["action"],"wrap_fb2_zip")
            self.assertEqual(item["books"][0]["name"],"book.fb2.zip")
            self.assertEqual(item["innerName"],"book.fb2")
            self.assertEqual(pb.canonical_upload_name(path),"book.fb2")
            with tempfile.TemporaryDirectory() as outdir:
                prepared=pb.materialize_upload_inputs([path],Path(outdir))
                out=Path(prepared["paths"][0])
                self.assertEqual(out.name,"book.fb2.zip")
                import zipfile
                with zipfile.ZipFile(out,"r") as zf:
                    self.assertEqual(zf.namelist(),["book.fb2"])
                    self.assertEqual(zf.read("book.fb2"),path.read_bytes())

    def test_direct_upload_rejects_fb2_xml_until_prepared(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"book.xml"
            path.write_text("<FictionBook><description/></FictionBook>",encoding="utf-8")
            client=pb.PocketBookClient(); client.session={"access_token":"x","refresh_token":"y","expires_at":9999999999}
            with self.assertRaises(pb.UnsafeOperation):
                client.upload(path)

    def test_tiny_epub_is_rejected_before_auth_or_network(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"tiny.epub"
            path.write_bytes(b"x"*1166)
            c=pb.PocketBookClient()
            with self.assertRaises(pb.UnsafeOperation) as cm:
                c.upload(path)
            self.assertIn("smaller than 4 KiB", str(cm.exception))

    def test_plain_xml_is_not_accepted_as_book(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"notbook.xml"
            path.write_text("<root><x/></root>",encoding="utf-8")
            self.assertFalse(pb.is_fb2_xml(path))
            with self.assertRaises(pb.UnsafeOperation):
                pb.inspect_upload_input(path)

    def test_inbound_resolve_exact_fb2_xml(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"media"/"inbound"/"stage"; root.mkdir(parents=True)
            path=root/"incoming.xml"
            path.write_text("<FictionBook><description/></FictionBook>",encoding="utf-8")
            result=pb.resolve_inbound_attachment("incoming.xml",within_seconds=60,roots=[Path(td)/"media"/"inbound"])
            self.assertTrue(result["ok"])
            self.assertEqual(Path(result["path"]).name,"incoming.xml")
            self.assertEqual(result["preflightItem"]["kind"],"fb2_xml")

    def test_upload_inbound_wraps_fb2_xml_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"media"/"inbound"/"stage"; root.mkdir(parents=True)
            src=root/"book.xml"
            original=b"<?xml version='1.0'?><FictionBook><description/></FictionBook>"
            src.write_bytes(original)
            class C:
                def upload(self, path):
                    path=Path(path)
                    self.uploaded_name=path.name
                    with zipfile.ZipFile(path,"r") as z:
                        self.entries=z.namelist()
                        self.inner=z.read(self.entries[0])
                    return {"ok":True,"verified":True,"name":path.name}
            import zipfile
            c=C()
            out=pb.upload_inbound(c,src,roots=[Path(td)/"media"/"inbound"])
            self.assertTrue(out["ok"])
            self.assertTrue(c.uploaded_name.endswith(".fb2.zip"))
            self.assertEqual(len(c.entries),1)
            self.assertTrue(c.entries[0].endswith(".fb2"))
            self.assertEqual(c.inner,original)
            self.assertTrue(out["cleanup"]["temporaryPreparedFilesRemoved"])

    def test_upload_inbound_many_ten_files_is_compact_and_no_confirmation(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"media"/"inbound"/"stage"; root.mkdir(parents=True)
            paths=[]
            for i in range(10):
                path=root/f"book-{i}.fb2"
                path.write_bytes((f"book-{i}"*20).encode())
                paths.append(path)
            class C:
                def __init__(self): self.calls=[]
                def upload(self,path):
                    path=Path(path); self.calls.append(path.name)
                    return {"ok":True,"verified":True,"name":path.name,"bytes":path.stat().st_size}
            c=C()
            out=pb.upload_inbound_many(c,paths,roots=[Path(td)/"media"/"inbound"])
            self.assertTrue(out["ok"])
            self.assertFalse(out["requiresConfirmation"])
            self.assertEqual(out["inputs"],10)
            self.assertEqual(out["books"],10)
            self.assertEqual(out["uploaded"],10)
            self.assertEqual(out["failed"],0)
            self.assertEqual(len(c.calls),10)
            self.assertEqual(len(out["files"]),10)
            self.assertTrue(all(set(row) <= {"filename","status"} for row in out["files"]))
            self.assertLess(len(json.dumps(out,ensure_ascii=False)),3000)

    def test_upload_inbound_many_validates_all_sources_before_upload(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); root=td/"inbound"; root.mkdir()
            good=root/"good.fb2"; good.write_bytes(b"ok")
            outside=td/"outside.fb2"; outside.write_bytes(b"no")
            class C:
                def __init__(self): self.calls=0
                def upload(self,path): self.calls+=1; return {"ok":True,"verified":True,"name":Path(path).name}
            c=C()
            with self.assertRaises(pb.UnsafeOperation):
                pb.upload_inbound_many(c,[good,outside],roots=[root])
            self.assertEqual(c.calls,0)

    def test_upload_inbound_many_over_ten_requires_confirmation_without_upload(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"inbound"; root.mkdir()
            paths=[]
            for i in range(11):
                path=root/f"book-{i}.fb2"; path.write_bytes(b"x"); paths.append(path)
            class C:
                def __init__(self): self.calls=0
                def upload(self,path): self.calls+=1; return {"ok":True,"verified":True,"name":Path(path).name}
            c=C()
            out=pb.upload_inbound_many(c,paths,roots=[root])
            self.assertFalse(out["ok"])
            self.assertTrue(out["requiresConfirmation"])
            self.assertEqual(out["preflight"]["books"],11)
            self.assertEqual(c.calls,0)

    def test_upload_inbound_rejects_non_inbound_path(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); allowed=td/"inbound"; allowed.mkdir()
            outside=td/"outside.fb2"; outside.write_bytes(b"<FictionBook/>")
            with self.assertRaises(pb.UnsafeOperation):
                pb.upload_inbound(object(),outside,roots=[allowed])

    def test_inbound_resolve_ambiguous_without_name(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"inbound"; root.mkdir()
            for name in ("a.epub","b.fb2"):
                (root/name).write_bytes(b"x")
            result=pb.resolve_inbound_attachment(None,within_seconds=60,roots=[root])
            self.assertFalse(result["ok"])
            self.assertTrue(result["ambiguous"])
            self.assertEqual(result["count"],2)

    def test_verify_uploaded_record_rejects_size_mismatch(self):
        book={"name":"book.epub","format":"epub","bytes":5000,"metadata":{"title":"Book"}}
        with self.assertRaises(pb.PocketBookError):
            pb.verify_uploaded_record(book,"book.epub",6000)

    def test_upload_many_marks_unverified_as_not_uploaded(self):
        class C:
            def upload(self,path):
                return {"ok":False,"accepted":True,"verified":False,"warning":"upload_accepted_but_not_yet_verified"}
        with tempfile.TemporaryDirectory() as td:
            q=Path(td)/"book.fb2"; q.write_bytes(b"<FictionBook/>")
            r=pb.upload_many(C(),[q])
        self.assertFalse(r["ok"])
        self.assertEqual(r["uploaded"],0)
        self.assertEqual(r["acceptedUnverified"],1)
        self.assertEqual(r["failed"],0)



    def test_download_for_chat_packages_original_bytes(self):
        import hashlib, zipfile
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            media=td/'media'/'pocketbook-cloud'
            book=raw_book(title='Book', author='A', fast='fh-chat', ident='42', name='book.fb2')
            book['format']='fb2'; book['bytes']=4; book['link']='https://files.example.test/book.fb2'
            class C:
                def books(self): return [book]
            fake_root=td/'media'
            fake_media=fake_root/'pocketbook-cloud-test-unit'
            fake_original=fake_media/'.work'/'book.fb2'
            fake_original.parent.mkdir(parents=True, exist_ok=True)
            fake_original.write_bytes(b'ABCD')
            def fake_download(_book, _out):
                return {'ok':True,'path':str(fake_original),'filename':'book.fb2','bytes':4}
            with patch.object(operations_mod,'download_book',fake_download), patch.object(operations_mod,'_media_roots',lambda:[fake_root.resolve()]):
                r=pb.download_for_chat(C(),'42',fake_media)
            self.assertTrue(r['ok'])
            self.assertTrue(r['packaged'])
            self.assertTrue(r['deliveryPending'])
            self.assertTrue(r['mediaPath'].endswith('.zip'))
            with zipfile.ZipFile(r['mediaPath']) as z:
                self.assertEqual(z.namelist(),['book.fb2'])
                self.assertEqual(z.read('book.fb2'),b'ABCD')


    def test_phonetic_title_fallback_revainder_to_rewinder(self):
        b=raw_book(title='Перемотчик', author='Brett Battles', fast='rw1', ident='42', name='Rewinder_GOLDEN.fb2')
        self.assertEqual(pb.resolve_book_phonetic_unique([b], 'ревайндер')['id'], '42')

    def test_phonetic_title_fallback_remains_ambiguous(self):
        a=raw_book(title='One', fast='rw1', ident='1', name='Rewinder.fb2')
        b=raw_book(title='Two', fast='rw2', ident='2', name='Rewinder_copy.fb2')
        with self.assertRaises(pb.AmbiguousBook):
            pb.resolve_book_phonetic_unique([a,b], 'ревайндер')


    def test_equivalent_outer_zip_pair_byte_identical_collapses(self):
        import zipfile
        raw=raw_book(title='Same', author='A', fast='raw', ident='1', name='book.fb2')
        raw['format']='fb2'
        wrapped=raw_book(title='Same', author='A', fast='zip', ident='2', name='book.fb2.zip')
        wrapped['format']='fb2.zip'
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); work=td/'work'; work.mkdir()
            rawp=work/'book.fb2'; rawp.write_bytes(b'original-bytes')
            zipp=work/'book.fb2.zip'
            with zipfile.ZipFile(zipp,'w',compression=zipfile.ZIP_STORED) as z:
                z.writestr('book.fb2',b'original-bytes')
            def fake_download(book, out):
                p=rawp if book['id']=='1' else zipp
                return {'ok':True,'path':str(p),'filename':p.name,'bytes':p.stat().st_size}
            with patch.object(operations_mod,'download_book',fake_download):
                selected, downloaded=pb._download_equivalent_packaging(None,[raw,wrapped],work)
            self.assertEqual(selected['id'],'1')
            self.assertEqual(Path(downloaded['path']).read_bytes(),b'original-bytes')

    def test_equivalent_outer_zip_pair_content_mismatch_does_not_collapse(self):
        import zipfile
        raw=raw_book(title='Same', author='A', fast='raw', ident='1', name='book.fb2')
        raw['format']='fb2'
        wrapped=raw_book(title='Same', author='A', fast='zip', ident='2', name='book.fb2.zip')
        wrapped['format']='fb2.zip'
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); work=td/'work'; work.mkdir()
            rawp=work/'book.fb2'; rawp.write_bytes(b'one')
            zipp=work/'book.fb2.zip'
            with zipfile.ZipFile(zipp,'w',compression=zipfile.ZIP_STORED) as z:
                z.writestr('book.fb2',b'two')
            def fake_download(book, out):
                p=rawp if book['id']=='1' else zipp
                return {'ok':True,'path':str(p),'filename':p.name,'bytes':p.stat().st_size}
            with patch.object(operations_mod,'download_book',fake_download):
                self.assertIsNone(pb._download_equivalent_packaging(None,[raw,wrapped],work))


if __name__ == "__main__": unittest.main(verbosity=2)
