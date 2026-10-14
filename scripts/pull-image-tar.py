"""Staging 機(有網路、沒有 docker)用:把任何 OCI registry 上的 image 存成 `docker load` 吃得下的 tar
用法:python pull-image-tar2.py <registry/repo:tag> <out.tar>
  例:python pull-image-tar2.py registry.k8s.io/sig-storage/nfsplugin:v4.13.4 nfsplugin.tar
只抓 linux/amd64;支援 Bearer token challenge(registry.k8s.io、ghcr.io、quay.io、docker.io)。
改自 vks-airgap 的 pull-image-tar.py(只支援 Docker Hub)。
"""
import gzip, hashlib, io, json, re, sys, tarfile, urllib.request, urllib.error

ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json"])


def parse(ref):
    first = ref.split("/")[0]
    if "." in first or ":" in first:
        reg, rest = first, ref[len(first) + 1:]
    else:
        reg, rest = "registry-1.docker.io", ref
        if "/" not in rest:
            rest = "library/" + rest
    if reg == "docker.io":
        reg = "registry-1.docker.io"
    repo, _, tag = rest.partition(":")
    return reg, repo, tag or "latest", ref


class Client:
    def __init__(self, reg, repo):
        self.reg, self.repo, self.token = reg, repo, None

    def get(self, url, accept=None, auth=True):
        req = urllib.request.Request(url)
        if accept:
            req.add_header("Accept", accept)
        if auth and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            return urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            if e.code == 401 and not self.token:
                h = e.headers.get("WWW-Authenticate", "")
                kv = dict(re.findall(r'(\w+)="([^"]*)"', h))
                q = f"{kv['realm']}?service={kv.get('service', '')}&scope=repository:{self.repo}:pull"
                self.token = json.load(urllib.request.urlopen(q, timeout=60)).get("token") or ""
                return self.get(url, accept, auth)
            raise


def main(ref, out):
    reg, repo, tag, full = parse(ref)
    c = Client(reg, repo)
    base = f"https://{reg}/v2/{repo}"
    m = json.load(c.get(f"{base}/manifests/{tag}", ACCEPT))
    if "manifests" in m:
        d = next(x for x in m["manifests"] if x.get("platform", {}).get("os") == "linux"
                 and x["platform"].get("architecture") == "amd64")
        m = json.load(c.get(f"{base}/manifests/{d['digest']}", ACCEPT))
    cfg_digest = m["config"]["digest"]
    cfg = c.get(f"{base}/blobs/{cfg_digest}").read()
    assert "sha256:" + hashlib.sha256(cfg).hexdigest() == cfg_digest, "config digest mismatch"
    diff_ids = json.loads(cfg)["rootfs"]["diff_ids"]
    layers = []
    with tarfile.open(out, "w") as tf:
        def add(name, data):
            ti = tarfile.TarInfo(name); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
        add(cfg_digest.split(":")[1] + ".json", cfg)
        for i, l in enumerate(m["layers"]):
            blob = c.get(f"{base}/blobs/{l['digest']}").read()
            assert "sha256:" + hashlib.sha256(blob).hexdigest() == l["digest"], "layer digest mismatch"
            raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
            assert "sha256:" + hashlib.sha256(raw).hexdigest() == diff_ids[i], "diff_id mismatch"
            name = diff_ids[i].split(":")[1] + "/layer.tar"
            add(name, raw); layers.append(name)
        add("manifest.json", json.dumps([{"Config": cfg_digest.split(":")[1] + ".json",
                                          "RepoTags": [full], "Layers": layers}]).encode())
    print(f"saved {full} -> {out} ({len(layers)} layers)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
