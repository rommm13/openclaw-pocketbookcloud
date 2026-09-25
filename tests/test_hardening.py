import io
import json
import os
import socket
import stat
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from test_pocketbook import FakeClient, pb, raw_book, valid_session
import pocketbook_lib.api as api_mod
import pocketbook_lib.operations as operations_mod
import pocketbook_lib.transport as transport_mod


class FakeHTTPResponse:
    def __init__(self, body=b"{}", status=200, headers=None, url="https://cloud.pocketbook.digital/x"):
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}
        self._url = url
    def read(self, size=-1): return self._body.read(size)
    def geturl(self): return self._url
    def getheader(self, name, default=None):
        for key, value in self.headers.items():
            if key.lower() == name.lower(): return value
        return default
    def __enter__(self): return self
    def __exit__(self, *args): return False

class FakeConn:
    def close(self): pass

class HardeningTests(unittest.TestCase):
    def test_session_corrupt_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"session.json"; path.write_text("{broken"); os.chmod(path,0o600)
            with self.assertRaises(pb.PocketBookError): pb.SessionStore(path).load()

    def test_session_invalid_field_type_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"session.json"; data=valid_session(); data["access_token"]=["bad"]
            path.write_text(json.dumps(data)); os.chmod(path,0o600)
            with self.assertRaises(pb.UnsafeOperation): pb.SessionStore(path).load()

    def test_session_wrong_permissions_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"session.json"; pb.SessionStore(path).save(valid_session()); os.chmod(path,0o644)
            with self.assertRaises(pb.UnsafeOperation): pb.SessionStore(path).load()

    def test_session_symlink_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); target=td/"real.json"; target.write_text(json.dumps(valid_session())); os.chmod(target,0o600)
            link=td/"session.json"; link.symlink_to(target)
            with self.assertRaises(pb.UnsafeOperation): pb.SessionStore(link).load()

    def test_session_clear_removes_pending_delete(self):
        with tempfile.TemporaryDirectory() as td:
            session=Path(td)/"session.json"; store=pb.SessionStore(session); store.save(valid_session())
            pending=session.with_name("delete-pending.json"); pb._private_json_write(pending,{"schema":4,"request":{}})
            store.clear(); self.assertFalse(session.exists()); self.assertFalse(pending.exists())

    def test_pending_delete_wrong_mode_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"delete-pending.json"
            pb._private_json_write(path,{"schema":4,"request":{"created_at":1,"expires_at":2,"fast_hash":"x","account_fingerprint":"y"}})
            os.chmod(path,0o644)
            with self.assertRaises(pb.UnsafeOperation): pb._load_delete_request(path)

    def test_delete_aborts_if_account_changes_between_turns(self):
        with tempfile.TemporaryDirectory() as td:
            c=FakeClient([raw_book()],Path(td)/"session.json"); pb.prepare_delete(c,"fh1")
            c.store.save(valid_session(email="other@example.com")); r=pb.confirm_delete(c,"удали")
            self.assertFalse(r["ok"]); self.assertEqual(r["reason"],"delete_account_changed"); self.assertEqual(c.deleted,[])

    def test_delete_aborts_if_id_changes_before_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            c=FakeClient([raw_book(fast="fh1",ident="1")],Path(td)/"session.json"); pb.prepare_delete(c,"fh1")
            c._books=[raw_book(fast="fh1",ident="changed")]
            with self.assertRaises(pb.UnsafeOperation): pb.confirm_delete(c,"удали")
            self.assertEqual(c.deleted,[])

    def test_conditional_delete_phrase_is_not_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            c=FakeClient([raw_book()],Path(td)/"session.json"); pb.prepare_delete(c,"fh1")
            r=pb.confirm_delete(c,"да, удали, но только если это не единственная копия")
            self.assertFalse(r["ok"]); self.assertEqual(r["reason"],"explicit_next_message_confirmation_required"); self.assertEqual(c.deleted,[])

    def test_delete_ack_without_disappearance_is_not_success(self):
        book=raw_book()
        class C(pb.PocketBookClient):
            def __init__(self): pass
            def ensure_token(self): pass
            def _request(self,*args,**kwargs): return pb.Response(200,{},b"","https://cloud.pocketbook.digital/delete")
            def books(self): return [book]
        with patch.object(api_mod.time,"sleep",lambda _:None): r=C().delete(book)
        self.assertFalse(r["ok"]); self.assertTrue(r["accepted"]); self.assertFalse(r["deleted"]); self.assertTrue(r["reconcileRequired"])

    def test_api_error_body_is_never_reflected(self):
        response=pb.Response(401,{},b'{"error_description":"password=supersecret"}',"https://cloud.pocketbook.digital/x")
        with self.assertRaises(pb.PocketBookError) as cm: pb.PocketBookClient._require_ok(response,"Login")
        self.assertNotIn("supersecret",str(cm.exception)); self.assertNotIn("password",str(cm.exception).casefold())

    def test_api_response_reader_enforces_limit(self):
        with self.assertRaises(pb.UnsafeOperation): pb._read_limited(io.BytesIO(b"x"*11),10,context="test")

    def test_api_contract_rejects_non_list_items(self):
        with self.assertRaises(pb.PocketBookError): pb._contract_items({"not":"list"},"books.items")

    def test_api_contract_rejects_negative_total(self):
        with self.assertRaises(pb.PocketBookError): pb._contract_nonnegative_int(-1,"books.total")

    def test_api_contract_ignores_unknown_fields(self):
        obj={"items":[],"future_field":{"anything":True}}; self.assertIs(pb._contract_dict(obj,"books"),obj)

    def test_api_request_disables_ambient_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            c=pb.PocketBookClient(pb.SessionStore(Path(td)/"session.json")); opener=MagicMock(); opener.open.return_value=FakeHTTPResponse()
            with patch.object(api_mod,"build_opener",return_value=opener) as build, patch.dict(os.environ,{"HTTPS_PROXY":"http://evil.invalid:8080"}):
                response=c._request("GET","/api/v1.0/test")
            self.assertEqual(response.status,200)
            handlers=[a for a in build.call_args.args if isinstance(a,api_mod.ProxyHandler)]
            self.assertEqual(len(handlers),1); self.assertEqual(handlers[0].proxies,{})

    def test_api_url_rejects_alternate_port(self):
        with tempfile.TemporaryDirectory() as td:
            c=pb.PocketBookClient(pb.SessionStore(Path(td)/"session.json"))
            with self.assertRaises(pb.UnsafeOperation): c._request("GET","https://cloud.pocketbook.digital:444/api/v1.0/user")

    def test_strict_api_redirect_rejects_url_credentials(self):
        h=pb.StrictApiRedirect(); req=pb.Request("https://cloud.pocketbook.digital/a")
        with self.assertRaises(pb.UnsafeOperation): h.redirect_request(req,None,302,"x",{},"https://u:p@cloud.pocketbook.digital/a")

    def test_public_https_rejects_alternate_port_before_dns(self):
        with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://example.com:444/file")

    def test_public_https_rejects_private_ipv4(self):
        info=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        with patch.object(transport_mod.socket,"getaddrinfo",return_value=info):
            with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://files.example.test/book")

    def test_public_https_rejects_private_ipv6(self):
        info=[(socket.AF_INET6,socket.SOCK_STREAM,6,"",("::1",443,0,0))]
        with patch.object(transport_mod.socket,"getaddrinfo",return_value=info):
            with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://files.example.test/book")

    def test_public_https_rejects_ipv4_mapped_private_ipv6(self):
        info=[(socket.AF_INET6,socket.SOCK_STREAM,6,"",("::ffff:127.0.0.1",443,0,0))]
        with patch.object(transport_mod.socket,"getaddrinfo",return_value=info):
            with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://files.example.test/book")

    def test_public_https_rejects_if_any_resolved_address_is_private(self):
        info=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),(socket.AF_INET,socket.SOCK_STREAM,6,"",("10.0.0.1",443))]
        with patch.object(transport_mod.socket,"getaddrinfo",return_value=info):
            with self.assertRaises(pb.UnsafeOperation): pb.ensure_public_https("https://files.example.test/book")

    def test_public_https_accepts_only_public_addresses(self):
        info=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))]
        with patch.object(transport_mod.socket,"getaddrinfo",return_value=info): pb.ensure_public_https("https://files.example.test/book")

    def test_malformed_large_epub_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.epub"; path.write_bytes(b"not-a-zip"+b"x"*5000)
            with self.assertRaises(pb.UnsafeOperation): pb.inspect_upload_input(path)

    def test_epub_missing_container_xml_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.epub"
            with zipfile.ZipFile(path,"w") as zf:
                zf.writestr("mimetype","application/epub+zip",compress_type=zipfile.ZIP_STORED); zf.writestr("padding.bin",b"x"*5000)
            with self.assertRaises(pb.UnsafeOperation): pb.inspect_upload_input(path)

    def test_fb2_false_fragment_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"fake.xml"; path.write_text("<root><!-- <FictionBook> --></root>")
            self.assertFalse(pb.is_fb2_xml(path))

    def test_fb2_doctype_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.xml"; path.write_text("<!DOCTYPE FictionBook [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><FictionBook/>")
            self.assertFalse(pb.is_fb2_xml(path))

    def test_native_fb2_zip_requires_exactly_one_book(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.fb2.zip"
            with zipfile.ZipFile(path,"w") as zf: zf.writestr("a.fb2","<FictionBook/>"); zf.writestr("b.fb2","<FictionBook/>")
            with self.assertRaises(pb.UnsafeOperation): pb.inspect_upload_input(path)

    def test_native_fb2_zip_rejects_plain_xml(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.fb2.zip"
            with zipfile.ZipFile(path,"w") as zf: zf.writestr("a.fb2","<root/>")
            with self.assertRaises(pb.UnsafeOperation): pb.inspect_upload_input(path)

    def test_transport_zip_rejects_special_fifo_entry(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"bad.zip"; info=zipfile.ZipInfo("book.fb2"); info.create_system=3; info.external_attr=(stat.S_IFIFO|0o600)<<16
            with zipfile.ZipFile(path,"w") as zf: zf.writestr(info,b"x")
            with self.assertRaises(pb.UnsafeOperation): pb.inspect_upload_input(path)

    def test_batch_duplicate_output_name_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); a=td/"Book.fb2"; bdir=td/"b"; bdir.mkdir(); b=bdir/"book.FB2"; a.write_bytes(b"a"); b.write_bytes(b"b")
            with self.assertRaises(pb.UnsafeOperation): pb.preflight_upload([a,b])

    def test_upload_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); real=td/"real.fb2"; real.write_bytes(b"<FictionBook/>"); link=td/"link.fb2"; link.symlink_to(real)
            c=pb.PocketBookClient(pb.SessionStore(td/"session.json")); c.session=valid_session()
            with self.assertRaises(pb.UnsafeOperation): c.upload(link)

    def test_upload_without_fast_hash_stays_unverified(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); path=td/"book.fb2"; path.write_bytes(b"<FictionBook/>")
            c=pb.PocketBookClient(pb.SessionStore(td/"session.json")); c.session=valid_session()
            with patch.object(c,"ensure_token",lambda:None), patch.object(c,"_upload_once",return_value=(200,{})), patch.object(c,"books",side_effect=AssertionError("must not verify")):
                r=c.upload(path)
            self.assertTrue(r["accepted"]); self.assertFalse(r["verified"]); self.assertFalse(r["ok"]); self.assertEqual(r["warning"],"upload_accepted_without_fast_hash")

    def test_upload_same_name_old_book_does_not_verify_new_hash(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); path=td/"book.fb2"; path.write_bytes(b"<FictionBook/>")
            old=raw_book(fast="old",name="book.fb2"); old["format"]="fb2"; old["bytes"]=path.stat().st_size
            c=pb.PocketBookClient(pb.SessionStore(td/"session.json")); c.session=valid_session()
            with patch.object(c,"ensure_token",lambda:None), patch.object(c,"_upload_once",return_value=(200,{"fast_hash":"new"})), patch.object(c,"books",return_value=[old]), patch.object(api_mod.time,"sleep",lambda _:None):
                r=c.upload(path)
            self.assertFalse(r["verified"]); self.assertEqual(r["fastHash"],"new")

    def test_verify_uploaded_record_rejects_hash_mismatch(self):
        book={"fast_hash":"old","name":"book.fb2","format":"fb2","bytes":10}
        with self.assertRaises(pb.PocketBookError): pb.verify_uploaded_record(book,"book.fb2",10,expected_fast_hash="new")

    def test_large_upload_batch_requires_confirmation_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            paths=[]
            for i in range(11): q=Path(td)/f"{i}.fb2"; q.write_bytes(b"x"); paths.append(q)
            class C:
                def __init__(self): self.calls=0
                def upload(self,path): self.calls+=1; return {"ok":True,"verified":True}
            c=C(); r=pb.upload_many(c,paths); self.assertTrue(r["requiresConfirmation"]); self.assertEqual(c.calls,0)

    def test_large_upload_batch_runs_after_confirmed(self):
        with tempfile.TemporaryDirectory() as td:
            paths=[]
            for i in range(11): q=Path(td)/f"{i}.fb2"; q.write_bytes(b"x"); paths.append(q)
            class C:
                def __init__(self): self.calls=0
                def upload(self,path): self.calls+=1; return {"ok":True,"verified":True,"name":Path(path).name}
            c=C(); r=pb.upload_many(c,paths,confirmed=True); self.assertTrue(r["ok"]); self.assertEqual(c.calls,11)

    def test_inbound_same_name_size_different_bytes_remain_ambiguous(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"inbound"; (root/"a").mkdir(parents=True); (root/"b").mkdir(); (root/"a"/"book.fb2").write_bytes(b"AAA"); (root/"b"/"book.fb2").write_bytes(b"BBB")
            r=pb.resolve_inbound_attachment("book.fb2",within_seconds=60,roots=[root]); self.assertFalse(r["ok"]); self.assertTrue(r["ambiguous"]); self.assertEqual(r["count"],2)

    def test_inbound_identical_staged_copies_collapse(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/"inbound"; (root/"a").mkdir(parents=True); (root/"b").mkdir()
            for folder in ("a","b"): (root/folder/"book.fb2").write_bytes(b"same")
            self.assertTrue(pb.resolve_inbound_attachment("book.fb2",within_seconds=60,roots=[root])["ok"])

    def test_untrusted_text_is_bounded_and_strips_control_characters(self):
        out=pb.safe_untrusted_text("hello\x00\x1bworld"+"x"*100,20); self.assertNotIn("\x00",out); self.assertNotIn("\x1b",out); self.assertLessEqual(len(out),20)

    def test_book_summary_does_not_emit_unbounded_title(self):
        self.assertLessEqual(len(pb.book_summary(raw_book(title="A"*5000))["title"]),512)

    def test_legacy_confirmation_cli_is_exit_zero(self):
        with patch.object(pb,"PocketBookClient",return_value=MagicMock()): self.assertEqual(pb.main(["delete","confirm","0C4B56"]),0)

class DownloadResponseTests(unittest.TestCase):
    def _book(self):
        b=raw_book(name="book.fb2"); b["format"]="fb2"; b["link"]="https://files.example.test/book.fb2"; return b
    def _run(self,response):
        with tempfile.TemporaryDirectory() as td, patch.object(transport_mod,"ensure_public_https",lambda _:None), patch.object(transport_mod,"_open_pinned_download",return_value=(FakeConn(),response,"https://files.example.test/book.fb2")):
            return pb.download_book(self._book(),Path(td))
    def test_download_rejects_invalid_content_length(self):
        with self.assertRaises(pb.DownloadError): self._run(FakeHTTPResponse(b"abc",headers={"Content-Length":"NaN"}))
    def test_download_rejects_negative_content_length(self):
        with self.assertRaises(pb.DownloadError): self._run(FakeHTTPResponse(b"abc",headers={"Content-Length":"-1"}))
    def test_download_rejects_non_identity_content_encoding(self):
        with self.assertRaises(pb.UnsafeOperation): self._run(FakeHTTPResponse(b"abc",headers={"Content-Encoding":"gzip"}))
    def test_download_detects_truncated_body(self):
        with self.assertRaises(pb.DownloadError): self._run(FakeHTTPResponse(b"abc",headers={"Content-Length":"5"}))
    def test_download_without_content_length_is_allowed_and_private(self):
        with tempfile.TemporaryDirectory() as td, patch.object(transport_mod,"ensure_public_https",lambda _:None), patch.object(transport_mod,"_open_pinned_download",return_value=(FakeConn(),FakeHTTPResponse(b"abc"),"https://files.example.test/book.fb2")):
            r=pb.download_book(self._book(),Path(td)); target=Path(r["path"]); self.assertEqual(target.read_bytes(),b"abc"); self.assertEqual(target.stat().st_mode&0o777,0o600)



class PinnedTransportTests(unittest.TestCase):
    def test_pinned_connection_uses_supplied_sockaddr_without_dns(self):
        info=(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))
        fake_sock=MagicMock(); context=MagicMock(); context.wrap_socket.return_value=fake_sock
        with patch.object(transport_mod.ssl,"create_default_context",return_value=context), patch.object(transport_mod.socket,"socket",return_value=fake_sock), patch.object(transport_mod.socket,"getaddrinfo",side_effect=AssertionError("second DNS resolution")):
            conn=pb._PinnedHTTPSConnection("files.example.test",info,3.0)
            conn.connect()
        fake_sock.connect.assert_called_once_with(("8.8.8.8",443))
        context.wrap_socket.assert_called_once_with(fake_sock,server_hostname="files.example.test")

    def test_pinned_download_sends_no_pocketbook_authorization(self):
        captured={}
        info=(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))
        parsed=transport_mod.urlparse("https://files.example.test/book.fb2")
        class Conn:
            def __init__(self,*args,**kwargs): pass
            def request(self,method,target,headers=None): captured.update(headers or {})
            def getresponse(self): return FakeHTTPResponse(b"ok",status=200,url="https://files.example.test/book.fb2")
            def close(self): pass
        with patch.object(transport_mod,"_public_https_addresses",return_value=(parsed,[info])), patch.object(transport_mod,"_PinnedHTTPSConnection",Conn):
            conn,response,url=pb._open_pinned_download("https://files.example.test/book.fb2",3.0)
        self.assertEqual(response.status,200)
        self.assertNotIn("Authorization",captured)
        self.assertEqual(captured.get("Accept-Encoding"),"identity")
        conn.close()

    def test_redirect_to_private_destination_is_rejected_before_second_connect(self):
        info=(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443))
        parsed=transport_mod.urlparse("https://files.example.test/book.fb2")
        class Conn:
            def __init__(self,*args,**kwargs): pass
            def request(self,*args,**kwargs): pass
            def getresponse(self): return FakeHTTPResponse(b"",status=302,headers={"Location":"https://private.example.test/steal"})
            def close(self): pass
        with patch.object(transport_mod,"_public_https_addresses",side_effect=[(parsed,[info]),pb.UnsafeOperation("private redirect")]), patch.object(transport_mod,"_PinnedHTTPSConnection",Conn):
            with self.assertRaises(pb.UnsafeOperation):
                pb._open_pinned_download("https://files.example.test/book.fb2",3.0)

    def test_upload_transport_rejects_file_replaced_after_validation(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); path=td/"book.fb2"; path.write_bytes(b"one"); before=path.lstat()
            replacement=td/"replacement.fb2"; replacement.write_bytes(b"two"); os.replace(replacement,path)
            c=pb.PocketBookClient(pb.SessionStore(td/"session.json")); c.session=valid_session()
            with patch.object(api_mod.http.client,"HTTPSConnection",return_value=MagicMock()):
                with self.assertRaises(pb.UnsafeOperation):
                    c._upload_once(path,"book.fb2",expected_stat=before)

    def test_private_json_write_rejects_symlink_state_directory(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td); real=td/"real"; real.mkdir(); os.chmod(real,0o700)
            link=td/"state"; link.symlink_to(real,target_is_directory=True)
            with self.assertRaises(pb.UnsafeOperation):
                pb._private_json_write(link/"pending.json",{"x":1})


if __name__ == "__main__": unittest.main(verbosity=2)
