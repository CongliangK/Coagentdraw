"""Bounded factual-research helpers. Read-only, standard library, no network.

Plans are search instructions, never evidence. Catalog verification and creative
choices are supplied by a human/agent; this module does not authenticate them.
"""
import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit

SCHEMA = "cwb-research-v1"
LIMIT = 1024 * 1024
MAX_RECORDS = 500
NOTICE = "离线辅助；核验与采用状态由输入者提供，程序未独立查证，不作美学判断，不联网或修改项目。"
SOURCES = {
    "aat": ("Getty AAT 术语", "https://www.getty.edu/research/tools/vocabularies/aat/", "术语/异名及文献线索；词条不是结构结论"),
    "wiki": ("Wikipedia 术语入口", "https://en.wikipedia.org/w/index.php", "仅找术语与引文，继续回到原机构或书页"),
    "commons": ("Wikimedia Commons 图片发现", "https://commons.wikimedia.org/w/index.php", "核对文件页、原馆藏号和各张图片的许可"),
    "books": ("Getty Research Portal 书目", "https://www.getty.edu/research-conservation/tools-databases/research-portal/", "目录发现；不等于全站全文检索，须进入托管机构查书页"),
    "institution": ("对应文化/器物的原机构", None, "由问题选择原机构；不能因接口容易访问而替换文化语境"),
}
ROUTES = {
    "term": ("aat", "wiki"), "form": ("commons", "institution"),
    "function": ("books", "institution"), "material": ("institution", "books"),
    "culture": ("institution", "books"),
}


def _text(value, name="text", *, empty=False):
    if (not isinstance(value, str) or (not empty and not value.strip())
            or len(value.encode("utf-8")) > 8192
            or any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value)):
        raise ValueError(name + " 须为非空文本（最多 8 KiB，无控制字符）")
    return value


def _number(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} 须为 {low}..{high} 的整数")
    return value


def _keys(value, required, optional=()):
    if (not isinstance(value, dict) or not set(required) <= set(value)
            or not set(value) <= set(required) | set(optional)):
        raise ValueError("缺失或未知字段；必填: " + ", ".join(required))


def _url(value):
    _text(value, "URL")
    parts = urlsplit(value)
    if (parts.scheme not in ("https", "http") or not parts.hostname
            or parts.username or parts.password or any(c.isspace() for c in value)):
        raise ValueError("来源/定位 URL 须为无凭据的 http(s) 地址")
    return value


def _normal(value):
    return unicodedata.normalize("NFKC", value).casefold()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("id 须为最多 64 字符的字母/数字/下划线/连字符")
    return value


def plan(question, kind, terms, *, sources=None, institution_url=None,
         candidates=5, reads=2, rewrites=2):
    _text(question, "具体决策问题")
    if kind not in ROUTES:
        raise ValueError("未知问题类型；美学参考不在事实检索范围内")
    if not isinstance(terms, list) or not 1 <= len(terms) <= 3:
        raise ValueError("须提供 1..3 个主词/异名，不自动猜术语")
    for term in terms:
        _text(term, "检索词")
    if len({_normal(t.strip()) for t in terms}) != len(terms):
        raise ValueError("检索词不可重复")
    _number(candidates, 1, 10, "candidates")
    _number(reads, 1, min(3, candidates), "reads")
    _number(rewrites, 0, 2, "rewrites")
    chosen = list(ROUTES[kind] if sources is None else sources)
    if not 1 <= len(chosen) <= 2 or len(set(chosen)) != len(chosen) or any(s not in SOURCES for s in chosen):
        raise ValueError("每次选 1..2 个不重复来源")
    if institution_url is not None:
        _url(institution_url)
        if "institution" not in chosen:
            raise ValueError("institution-url 仅用于已选择的 institution 来源")
    routes = []
    for source in chosen:
        name, entry, boundary = SOURCES[source]
        if source == "institution":
            entry = institution_url
        queries = []
        for term in terms:
            # Each alias is a separate query; do not AND unrelated alternatives.
            url = entry
            if source in ("wiki", "commons"):
                url += "?" + urlencode({"search": term, "title": "Special:Search"})
            queries.append({"term": term, "url": url, "action": "search" if source in ("wiki", "commons") else "enter_term_at_source"})
        routes.append({"source": source, "name": name, "queries": queries, "boundary": boundary})
    return {"schema": "cwb-research-plan-v1", "question": question, "kind": kind,
            "status": "planned_not_searched", "routes": routes,
            "budget": {"candidate_objects_total": candidates, "full_reads_total": reads, "rewrite_rounds_max": rewrites},
            "gaps": ["请先选对应文化/器物的原机构入口"] if "institution" in chosen and institution_url is None else [],
            "stop_when": ["足以作当前决策且无未处理重大矛盾", "未见部分已标出，新增图片预计不改变选择", f"达到 {rewrites} 轮改写预算仍无新依据时记录缺口；不生成图片冒充证据"],
            "notice": NOTICE}


def validate(data):
    _keys(data, ("schema", "question", "records"))
    if data["schema"] != SCHEMA:
        raise ValueError("未知 research schema")
    question = data["question"]
    _keys(question, ("id", "decision", "intent_version"))
    _id(question["id"])
    _text(question["decision"], "决策问题")
    _text(question["intent_version"], "意图版本")
    records = data["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise ValueError("records 须为列表，最多 500 条")
    ids = set()
    for record in records:
        _keys(record, ("id", "source", "locator", "terms", "excerpt", "claims", "verification", "rights", "decision"), ("access_url",))
        if _id(record["id"]) in ids:
            raise ValueError("record id 重复")
        ids.add(record["id"])
        source = record["source"]
        _keys(source, ("institution", "title", "url"), ("object_id", "edition"))
        for key in ("institution", "title"):
            _text(source[key], key)
        _url(source["url"])
        for key in ("object_id", "edition"):
            if key in source:
                _text(source[key], key)
        if "access_url" in record:
            _url(record["access_url"])
        locator = record["locator"]
        _keys(locator, (), ("url", "path", "pdf_page", "printed_page", "figure", "region", "canvas", "section"))
        for key, value in locator.items():
            if key == "pdf_page":
                _number(value, 1, 1000000, key)
            elif key in ("url", "canvas"):
                _url(value)
            else:
                _text(value, key)
            if key == "path" and (PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts or "\\" in value):
                raise ValueError("locator.path 须为相对目录下的资料路径，不含 ..")
        known_path = "path" in locator and _normal(locator["path"].strip()) != "unknown"
        if "pdf_page" in locator and not ("url" in locator or known_path):
            raise ValueError("PDF 页码须同时给出 locator.url 或非 unknown 的 locator.path；不自动回退 source.url")
        if "path" in locator and "pdf_page" not in locator:
            raise ValueError("本地书页定位须给出 pdf_page")
        if {"pdf_page", "printed_page", "figure"} & locator.keys() and "edition" not in source:
            raise ValueError("书页/图版须记录 source.edition；未知版本写 unknown")
        terms = record["terms"]
        if not isinstance(terms, list) or len(terms) > 32:
            raise ValueError("terms 须为最多 32 项的列表")
        for term in terms:
            _text(term, "term")
        _text(record["excerpt"], "excerpt", empty=True)
        claims = record["claims"]
        _keys(claims, ("fact", "observation", "inference", "unknown"))
        for values in claims.values():
            if not isinstance(values, list) or len(values) > 32:
                raise ValueError("每类判断须为最多 32 项的文本列表")
            for value in values:
                _text(value, "判断")
        checked = record["verification"]
        _keys(checked, ("text_read", "image_seen"))
        if any(type(v) is not bool for v in checked.values()):
            raise ValueError("核验状态须为 bool")
        if claims["fact"] and not checked["text_read"]:
            raise ValueError("fact 须有原文已读声明；未读只保留线索/未知")
        if claims["observation"] and not checked["image_seen"]:
            raise ValueError("observation 须有图像已看声明")
        _keys(record["rights"], ("metadata", "text", "image"))
        for value in record["rights"].values():
            _text(value, "权利标签（未知写 unknown）")
        decision = record["decision"]
        _keys(decision, ("status", "reason"))
        if decision["status"] not in ("candidate", "accepted", "rejected"):
            raise ValueError("decision.status 须为 candidate/accepted/rejected")
        _text(decision["reason"], "采用/驳回/待查原因")
        if decision["status"] == "accepted":
            if not (claims["fact"] or claims["observation"]):
                raise ValueError("采用为事实依据须有事实或可见观察，推断不能单独充当证据")
            known_object = _normal(source.get("object_id", "").strip()) not in ("", "unknown")
            known_locator = any(not isinstance(value, str) or _normal(value.strip()) != "unknown"
                                for value in locator.values())
            if not known_locator and not known_object:
                raise ValueError("已采用依据须有对象号或精确定位")
    return data


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 重复字段: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("JSON 非有限值: " + value)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMIT:
            raise ValueError("只读取不超过 1 MiB 的普通 JSON 文件")
        raw = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > LIMIT or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("文件过大或读取期间变化")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("须为有效 UTF-8 JSON，且嵌套不能过深") from exc
    return validate(data), hashlib.sha256(raw).hexdigest()


def _identity(record):
    source = record["source"]
    edition = _normal(source.get("edition", "").strip())
    if edition == "unknown":
        edition = ""
    institution = _normal(source["institution"].strip())
    object_id = _normal(source.get("object_id", "").strip())
    # Unknown labels are missing identity, never a shared original object.
    if object_id and object_id != "unknown" and institution != "unknown":
        return ("object", institution, object_id, edition)
    parts = urlsplit(source["url"])
    # Preserve query/path case; only fragments and hostname case are non-identity.
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
    return ("url", url, edition)


def _locators(record):
    locator = dict(record["locator"])
    if "url" in locator and "pdf_page" in locator:
        base = locator["url"].split("#", 1)[0]
        locator["open_url"] = base + "#page=" + str(locator["pdf_page"])
    return {"source_url": record["source"]["url"], "access_url": record.get("access_url"), **locator}


def _status(record):
    status = record["decision"]["status"]
    if status == "accepted":
        return "adopted_user_supplied"
    if status == "rejected":
        return "rejected_user_supplied"
    return "verified_user_supplied" if any(record["verification"].values()) else "candidate"


def search(data, terms, *, limit=5):
    validate(data)
    _number(limit, 1, 10, "limit")
    if not isinstance(terms, list) or not 1 <= len(terms) <= 8:
        raise ValueError("查询需 1..8 个词/短语；中文短语可直接子串匹配")
    terms = list(dict.fromkeys(_normal(_text(t, "查询词").strip()) for t in terms))
    groups = {}
    for record in data["records"]:
        if record["decision"]["status"] == "rejected":
            continue
        title = _normal(record["source"]["title"])
        aliases = [_normal(term) for term in record["terms"]]
        # Inferences/unknowns never improve relevance as factual evidence.
        body = _normal(" ".join([record["excerpt"], *record["claims"]["fact"], *record["claims"]["observation"]]))
        scores = [4 if term in aliases else 3 if term in title else 1 if term in body else 0 for term in terms]
        if not any(scores):
            continue
        item = {"record": record, "score": sum(scores), "matched_terms": [t for t, score in zip(terms, scores) if score]}
        groups.setdefault(_identity(record), []).append(item)
    ranked = []
    for matches in groups.values():
        matches.sort(key=lambda m: (-m["score"], m["record"]["id"]))
        best = matches[0]
        record = best["record"]
        ranked.append({"id": record["id"], "score": best["score"], "matched_terms": best["matched_terms"],
                       "title": record["source"]["title"], "locator": _locators(record),
                       "decision": record["decision"], "verification": record["verification"], "evidence_status": _status(record),
                       "alternative_record_ids": [m["record"]["id"] for m in matches[1:]]})
    ranked.sort(key=lambda m: (-m["score"], m["id"]))
    return {"question": data["question"], "status": "local_candidates_not_new_verification",
            "matching_source_groups": len(ranked), "omitted_source_groups": max(0, len(ranked) - limit),
            "results": ranked[:limit], "notice": NOTICE}


def inspect(data, record_id):
    validate(data)
    for record in data["records"]:
        if record["id"] == record_id:
            return {"question": data["question"], "record": record, "evidence_status": _status(record), "open_locator": _locators(record),
                    "same_source_ids": sorted(r["id"] for r in data["records"] if _identity(r) == _identity(record)),
                    "notice": NOTICE}
    raise ValueError("没有该 record id")


def evidence(data, *, stop_reason, outcome):
    validate(data)
    _text(stop_reason, "停止/缺口原因")
    if outcome not in ("sufficient", "blocked"):
        raise ValueError("outcome 须为 sufficient/blocked（输入者判断）")
    groups = {}
    pending, rejected = [], []
    for record in data["records"]:
        status = record["decision"]["status"]
        if status == "accepted":
            groups.setdefault(_identity(record), []).append(record)
        else:
            (pending if status == "candidate" else rejected).append({
                "id": record["id"], "reason": record["decision"]["reason"],
                "unknown": list(record["claims"]["unknown"])})
    if outcome == "sufficient" and not groups:
        raise ValueError("没有采用的事实依据，不能声明 sufficient")
    return {"question": data["question"], "outcome_user_supplied": outcome, "stop_reason": stop_reason,
            "accepted_source_groups": [sorted(group, key=lambda r: r["id"]) for _, group in sorted(groups.items())],
            "accepted_group_count": len(groups), "pending": pending, "rejected": rejected,
            "budget_warning": "超过 3 项主依据；请说明覆盖缺口或收窄问题，未静默截断" if len(groups) > 3 else None,
            "notice": NOTICE}


def build_parser():
    parser = argparse.ArgumentParser(description="事实检索：来源计划/本地关键词检索/精确定位；不联网、不写文件")
    commands = parser.add_subparsers(dest="research_cmd", required=True)
    p = commands.add_parser("plan", help="输出有预算的来源查询入口，未执行联网搜索")
    p.add_argument("question", help="这次要决定什么；一次一个阻塞问题")
    p.add_argument("--kind", choices=ROUTES, required=True)
    p.add_argument("--term", action="append", required=True, help="主词或异名，最多 3 项，各自查询")
    p.add_argument("--source", choices=SOURCES, action="append", help="覆盖默认 1..2 个来源")
    p.add_argument("--institution-url", help="对应文化/器物的原机构入口；不会访问")
    p.add_argument("--candidates", type=int, default=5)
    p.add_argument("--reads", type=int, default=2)
    p.add_argument("--rewrites", type=int, default=2)
    for command in ("search", "inspect", "evidence"):
        p = commands.add_parser(command)
        p.add_argument("catalog", help="用户/智能体已整理的页级/对象 JSON，最多 1 MiB，不读取所指原图/PDF")
        if command == "search":
            p.add_argument("--term", action="append", required=True, help="OR 词/短语；最多 8 项")
            p.add_argument("--limit", type=int, default=5, help="最多 10 个去重对象/书籍来源")
        elif command == "inspect":
            p.add_argument("id")
        else:
            p.add_argument("--outcome", choices=("sufficient", "blocked"), required=True)
            p.add_argument("--stop-reason", required=True)

    return parser


def run(args):
    try:
        if args.research_cmd == "plan":
            result = plan(args.question, args.kind, args.term, sources=args.source, institution_url=args.institution_url,
                          candidates=args.candidates, reads=args.reads, rewrites=args.rewrites)
        else:
            data, digest = load(args.catalog)
            if args.research_cmd == "search":
                result = search(data, args.term, limit=args.limit)
            elif args.research_cmd == "inspect":
                result = inspect(data, args.id)
            else:
                result = evidence(data, outcome=args.outcome, stop_reason=args.stop_reason)
            result["catalog_sha256"] = digest
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return result
    except (ValueError, OSError) as exc:
        raise SystemExit("research: " + str(exc)) from exc


def main(argv=None):
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
