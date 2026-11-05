# -*- coding: utf-8 -*-
"""不依赖 fnpack 的 FPK 手工重建脚本。

用途：本机没有 fnpack.exe（或无法连网下载）时，用已有的旧 .fpk 作模板，
只替换内层 app.tgz 与 manifest，其余（cmd/、config/、wizard/、图标）原样保留。

用法：
    python fpk/build-fpk-manual.py                    # 自动找模板、按 manifest 里的 version 命名
    python fpk/build-fpk-manual.py --template fpk/webvox-mcp-1.6.0.fpk

产物：fpk/webvox-mcp-<manifest.version>.fpk
"""

import argparse
import io
import tarfile
from pathlib import Path

FPK_DIR = Path(__file__).resolve().parent
APP_DIR = FPK_DIR / "app"

# 与 1.6.0 包内 app.tgz 的成员顺序保持一致，避免不必要的字节差异
APP_ORDER = [
    "docker",
    "docker/docker-compose.yaml",
    "python",
    "python/config_manager.py",
    "python/mcp_pipe.py",
    "python/service.py",
    "python/start.sh",
    "python/www",
    "python/www/index.html",
    "python/www/logs.html",
    "python/联网查询.py",
    "ui",
    "ui/config",
    "ui/images",
    "ui/images/icon_256.png",
    "ui/images/icon_64.png",
    "config",
    "config/privilege",
    "config/resource",
]


def read_version(manifest_path):
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("manifest 里没有 version")


# 容器内 / fnOS 宿主机上要执行的脚本，必须是 LF。
# Windows 上 checkout 或编辑会变成 CRLF，dash 会报 "set: Illegal option -"，
# 所以打包前一律归一化，不信任工作区字节。
SHELL_MEMBERS = {
    "python/start.sh",
    "wizard/install",
    "config/privilege",
    "config/resource",
}
SHELL_PREFIXES = ("cmd/",)


def is_shell_member(name):
    return name in SHELL_MEMBERS or name.startswith(SHELL_PREFIXES)


def to_lf(data):
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def pick_template(explicit=None):
    if explicit:
        path = Path(explicit)
        if not path.exists():
            raise SystemExit(f"模板不存在: {path}")
        return path
    candidates = sorted(FPK_DIR.glob("webvox-mcp-*.fpk"))
    if not candidates:
        raise SystemExit("fpk 目录下没有可作模板的 webvox-mcp-*.fpk")
    return candidates[0]


def build_app_tgz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        seen = set()
        for rel in APP_ORDER:
            path = APP_DIR / rel
            if not path.exists():
                # config/privilege 等来自 fpk/config，不在 fpk/app 下
                path = FPK_DIR / rel
            if not path.exists():
                raise SystemExit(f"缺少文件: {rel}")
            info = tar.gettarinfo(str(path), arcname=rel)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if info.isdir():
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.mode = 0o755 if rel.endswith(".sh") else 0o644
                with open(path, "rb") as fh:
                    data = fh.read()
                if is_shell_member(rel):
                    data = to_lf(data)
                tar.addfile(info, io.BytesIO(data))
            seen.add(rel)

        for path in sorted(APP_DIR.rglob("*")):
            rel = path.relative_to(APP_DIR).as_posix()
            if rel in seen or "__pycache__" in rel:
                continue
            print("补充未列出的文件:", rel)
            info = tar.gettarinfo(str(path), arcname=rel)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if info.isdir():
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.mode = 0o644
                with open(path, "rb") as fh:
                    data = fh.read()
                if is_shell_member(rel):
                    data = to_lf(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", help="旧 fpk 路径（默认取 fpk 下第一个）")
    parser.add_argument("--out", help="输出路径（默认按 manifest.version 命名）")
    args = parser.parse_args()

    template = pick_template(args.template)
    version = read_version(FPK_DIR / "manifest")
    out = Path(args.out) if args.out else FPK_DIR / f"webvox-mcp-{version}.fpk"
    if out.resolve() == template.resolve():
        raise SystemExit("输出路径不能覆盖模板")

    app_tgz = build_app_tgz()
    manifest_bytes = (FPK_DIR / "manifest").read_bytes()

    with tarfile.open(template, "r:gz") as src, tarfile.open(
        out, "w:gz", format=tarfile.GNU_FORMAT
    ) as dst:
        app_info = src.getmember("app.tgz")
        app_info.size = len(app_tgz)
        app_info.mtime = 0
        app_info.uid = app_info.gid = 0
        app_info.uname = app_info.gname = ""
        dst.addfile(app_info, io.BytesIO(app_tgz))

        for member in src.getmembers():
            if member.name in ("app.tgz", "manifest"):
                continue
            data = src.extractfile(member).read() if member.isfile() else None
            if data is not None and is_shell_member(member.name):
                data = to_lf(data)
            member.mtime = 0
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            dst.addfile(member, io.BytesIO(data) if data is not None else None)

        man_info = src.getmember("manifest")
        man_info.size = len(manifest_bytes)
        man_info.mtime = 0
        man_info.uid = man_info.gid = 0
        man_info.uname = man_info.gname = ""
        dst.addfile(man_info, io.BytesIO(manifest_bytes))

    print(f"已生成 {out}（{out.stat().st_size} 字节，版本 {version}）")
    verify(out)


def verify(path):
    with tarfile.open(path, "r:gz") as outer:
        assert "app.tgz" in outer.getnames()
        manifest = outer.extractfile("manifest").read().decode("utf-8")
        inner_data = outer.extractfile("app.tgz").read()
        for member in outer.getmembers():
            if member.isfile() and is_shell_member(member.name):
                raw = outer.extractfile(member).read()
                assert b"\r" not in raw, f"{member.name} 含 CRLF，dash 会起不来"
    with tarfile.open(fileobj=io.BytesIO(inner_data), mode="r:gz") as inner:
        names = set(inner.getnames())
        for need in ("python/service.py", "python/联网查询.py", "python/www/index.html"):
            assert need in names, f"缺少 {need}"
        for name in names:
            if name.endswith(".sh") or is_shell_member(name):
                raw = inner.extractfile(name).read()
                assert b"\r" not in raw, f"{name} 含 CRLF，dash 会起不来"
    version = [l for l in manifest.splitlines() if l.startswith("version")][0]
    print("自检通过：", version, "（脚本行尾全部 LF）")


if __name__ == "__main__":
    main()
