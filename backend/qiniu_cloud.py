# -*- coding: utf-8 -*-
"""七牛云 Kodo 对象存储客户端，仅用标准库实现，不依赖七牛 SDK。"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

RS_HOST = "https://rs.qiniuapi.com"
UPLOAD_HOSTS = {
    "z0": "https://upload.qiniup.com",
    "z1": "https://upload-z1.qiniup.com",
    "z2": "https://upload-z2.qiniup.com",
    "na0": "https://upload-na0.qiniup.com",
    "as0": "https://upload-as0.qiniup.com",
}


def urlsafe_b64(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


class QiniuCloud:
    def __init__(self, access_key="", secret_key="", bucket="", domain="", region="z0", private=False):
        self.access_key = (access_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.bucket = (bucket or "").strip()
        self.domain = (domain or "").strip().rstrip("/")
        self.region = (region or "z0").strip()
        self.private = bool(private)

    @property
    def configured(self):
        return bool(self.access_key and self.secret_key and self.bucket and self.domain)

    def _sign(self, data):
        return urlsafe_b64(hmac.new(self.secret_key.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest())

    def _entry_uri(self, key):
        return urlsafe_b64(self.bucket + ":" + key)

    def upload_token(self, key="", expires=3600):
        scope = self.bucket + (":" + key if key else "")
        policy = json.dumps({"scope": scope, "deadline": int(time.time()) + int(expires)}, separators=(",", ":"))
        encoded = urlsafe_b64(policy)
        return "{}:{}:{}".format(self.access_key, self._sign(encoded), encoded)

    def upload_file(self, key, fileobj, filename=None, content_type="application/octet-stream"):
        host = UPLOAD_HOSTS.get(self.region, UPLOAD_HOSTS["z0"])
        boundary = uuid.uuid4().hex
        token = self.upload_token(key)
        filename = (filename or key.rsplit("/", 1)[-1] or "file").replace('"', "")
        parts = []
        for field in (("token", token), ("key", key)):
            parts.append(("--" + boundary + "\r\n").encode("utf-8"))
            parts.append(("Content-Disposition: form-data; name=\"" + field[0] + "\"\r\n\r\n").encode("utf-8"))
            parts.append(field[1].encode("utf-8"))
            parts.append(b"\r\n")
        parts.append(("--" + boundary + "\r\n").encode("utf-8"))
        parts.append(("Content-Disposition: form-data; name=\"file\"; filename=\"" + filename + "\"\r\nContent-Type: " + content_type + "\r\n\r\n").encode("utf-8"))
        parts.append(fileobj.read())
        parts.append(("\r\n--" + boundary + "--\r\n").encode("utf-8"))
        code, text = self._request("POST", host, body=b"".join(parts),
                                   headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
                                   timeout=120)
        if code >= 400:
            raise RuntimeError(text[:300] or ("上传失败: " + str(code)))
        return json.loads(text)

    def _request(self, method, url, headers=None, body=None, timeout=30):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as e:
            raise RuntimeError(str(e))

    def _mgmt(self, method, path, timeout=30):
        headers = {"Authorization": "QBox {}:{}".format(self.access_key, self._sign(path))}
        code, text = self._request(method, RS_HOST + path, headers=headers, timeout=timeout)
        if code >= 400:
            raise RuntimeError(text[:300] or ("云存储请求失败: " + str(code)))
        return json.loads(text) if text.strip() else {}

    def list_files(self, prefix="", limit=100, marker=None):
        params = {"bucket": self.bucket, "limit": min(int(limit or 100), 1000)}
        if prefix:
            params["prefix"] = prefix
        if marker:
            params["marker"] = marker
        path = "/bucket?" + urllib.parse.urlencode(params)
        data = self._mgmt("GET", path)
        return data.get("items", []), data.get("marker"), data.get("commonPrefixes", [])

    def stat_file(self, key):
        return self._mgmt("POST", "/stat/" + self._entry_uri(key))

    def delete_file(self, key):
        return self._mgmt("POST", "/delete/" + self._entry_uri(key))

    def download_url(self, key, expires=3600):
        base = "https://{}/{}".format(self.domain, key)
        if not self.private:
            return base
        deadline = int(time.time()) + int(expires)
        url = base + "?e=" + str(deadline)
        return url + "&token=" + self.access_key + ":" + self._sign(url)

    def get_json(self, key, timeout=20):
        try:
            code, text = self._request("GET", self.download_url(key), timeout=timeout)
            if code != 200:
                return None
            return json.loads(text)
        except Exception:
            return None

