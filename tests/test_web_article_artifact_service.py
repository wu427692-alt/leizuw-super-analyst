from hashlib import sha256
from pathlib import Path

import pytest

from src.services.industry_research_service import IndustryResearchService
from src.services.industry_research_sources import IndustryResearchSourceCollector
from src.services.web_article_artifact_service import (
    WebArticleArtifactError,
    WebArticleArtifactService,
)


class _FakeResponse:
    def __init__(self, body=b"", *, status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, _size):
        midpoint = max(1, len(self.body) // 2)
        yield self.body[:midpoint]
        yield self.body[midpoint:]


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _article_html(subject="光模块"):
    paragraphs = "".join(
        f"<p>{subject}产业链第{index}部分：需求、技术、产能、价格和风险需要结合公开数据持续核验。</p>"
        for index in range(30)
    )
    return f"<html><head><title>{subject}行业研究</title></head><body><nav>菜单</nav><article><h1>{subject}行业研究</h1>{paragraphs}</article></body></html>".encode()


def _spa_shell(script_src="/assets/index-company.js", *, second_src=""):
    second = f'<script type="module" src="{second_src}"></script>' if second_src else ""
    return (
        "<html><head><title>华懋科技</title></head><body><div id='root'></div>"
        f'<script type="module" src="{script_src}"></script>{second}</body></html>'
    ).encode()


def _hmt_company_module():
    paragraphs = [
        "\\u534e\\u61cb科技成立于2002年，是一家专注于汽车安全系统部件研发、生产和销售的高新技术企业，公司持续推进技术创新与全球化布局。",
        "股票代码：603306（上交所），公司坚持以客户需求为导向，构建长期稳定的研发、制造与质量体系。",
        "公司围绕被动安全、新材料和智能制造持续投入，产品服务国内外主要客户，并重视环境责任与人才发展。",
        "未来公司将继续提升核心竞争力，以可靠产品和专业服务创造长期价值，相关经营信息以正式公告为准。",
        "研发团队持续改善产品性能、制造效率和质量控制能力，并通过规范治理保障经营信息真实、准确和完整。",
        "公司坚持长期主义和审慎经营原则，持续关注客户需求、行业技术演进、供应链韧性和可持续发展。",
        "面向未来，公司将以创新驱动业务升级，深化产业协作，并通过公开披露向投资者说明经营进展与风险。",
    ]
    extras = [
        "linear-gradient to right animation transform @keyframes company visual template",
        "The company was founded in 2010, employs more than 5000 people and reports annual revenue above ten billion dollars.",
    ]
    return (
        "const profile=" + ",".join(f'\"{value}\"' for value in [*paragraphs, *extras]) + ";"
    ).encode()


def test_web_article_fetch_is_bounded_hashed_and_cached(tmp_path: Path):
    session = _FakeSession([_FakeResponse(_article_html())])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    first = service.fetch_text("https://research.example.com/optical?id=1#section")
    second = service.fetch_text("https://research.example.com/optical?id=1")

    assert first["cached"] is False
    assert second["cached"] is True
    assert len(session.calls) == 1
    assert first["evidence_id"].startswith("web_fulltext:")
    assert first["document_hash"] and first["text_hash"]
    assert first["document_bytes"] > 0 and first["text_chars"] >= 300
    assert "光模块产业链" in first["text"]
    assert "菜单" not in first["text"]


def test_web_article_rejects_private_dns_before_request(tmp_path: Path):
    session = _FakeSession([])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.com"],
        resolver=lambda _host: ["127.0.0.1"],
    )

    with pytest.raises(WebArticleArtifactError, match="内网或保留地址"):
        service.fetch_text("https://example.com/article")
    with pytest.raises(WebArticleArtifactError, match="HTTPS 公网域名"):
        service.fetch_text("http://example.com/article")
    assert session.calls == []


def test_web_article_rejects_cross_allowlist_redirect_and_non_html(tmp_path: Path):
    redirect_session = _FakeSession([
        _FakeResponse(status=302, headers={"Location": "https://other.example.org/article"}),
    ])
    service = WebArticleArtifactService(
        session=redirect_session,
        cache_dir=tmp_path / "redirect",
        allowed_hosts=["example.com", "example.org"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    with pytest.raises(WebArticleArtifactError, match="跨域重定向"):
        service.fetch_text("https://example.com/article")

    mime_session = _FakeSession([
        _FakeResponse(b"%PDF-not-html", headers={"Content-Type": "application/pdf"}),
    ])
    mime_service = WebArticleArtifactService(
        session=mime_session,
        cache_dir=tmp_path / "mime",
        allowed_hosts=["example.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    with pytest.raises(WebArticleArtifactError, match="HTML MIME"):
        mime_service.fetch_text("https://example.com/article")

    oversized_session = _FakeSession([
        _FakeResponse(
            _article_html(),
            headers={"Content-Type": "text/html", "Content-Length": str(3 * 1024 * 1024)},
        ),
    ])
    oversized_service = WebArticleArtifactService(
        session=oversized_session,
        cache_dir=tmp_path / "oversized",
        allowed_hosts=["example.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    with pytest.raises(WebArticleArtifactError, match="大小上限"):
        oversized_service.fetch_text("https://example.com/article")


@pytest.mark.parametrize("javascript_mime", ["application/javascript; charset=utf-8", "text/javascript"])
def test_exact_company_spa_reads_one_same_origin_module_and_caches_auditable_hashes(
    tmp_path: Path,
    javascript_mime: str,
):
    shell = _spa_shell()
    module = _hmt_company_module()
    session = _FakeSession([
        _FakeResponse(shell),
        _FakeResponse(module, headers={"Content-Type": javascript_mime}),
    ])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    first = service.fetch_text(
        "https://www.hmtnew.com/",
        allow_same_origin_module_fallback=True,
    )
    second = service.fetch_text(
        "https://www.hmtnew.com/",
        allow_same_origin_module_fallback=True,
    )

    assert len(session.calls) == 2
    assert first["cached"] is False and second["cached"] is True
    assert first["extraction_method"] == "same_origin_module_static_strings"
    assert first["asset_url"] == "https://www.hmtnew.com/assets/index-company.js"
    assert first["asset_content_type"] == javascript_mime
    assert first["html_document_hash"] == sha256(shell).hexdigest()
    assert first["asset_document_hash"] == sha256(module).hexdigest()
    assert first["document_hash"] not in {
        first["html_document_hash"], first["asset_document_hash"],
    }
    assert first["document_bytes"] == len(shell) + len(module)
    assert first["evidence_id"] == second["evidence_id"]
    assert first["document_hash"] == second["document_hash"]
    assert "华懋科技" in first["text"] and "603306" in first["text"]
    assert "linear-gradient" not in first["text"]
    assert "founded in 2010" not in first["text"]
    assert first["text_chars"] >= service.min_text_chars
    assert first["module_documents"][0]["document_hash"] == first["asset_document_hash"]


def test_spa_fallback_is_default_off_and_family_allowlist_does_not_enable_it(tmp_path: Path):
    shell = _spa_shell()
    default_session = _FakeSession([_FakeResponse(shell)])
    exact_service = WebArticleArtifactService(
        session=default_session,
        cache_dir=tmp_path / "default-off",
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    with pytest.raises(WebArticleArtifactError, match="正文过短"):
        exact_service.fetch_text("https://www.hmtnew.com/")
    assert len(default_session.calls) == 1

    family_session = _FakeSession([_FakeResponse(shell)])
    family_service = WebArticleArtifactService(
        session=family_session,
        cache_dir=tmp_path / "family-only",
        allowed_hosts=["hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    with pytest.raises(WebArticleArtifactError, match="正文过短"):
        family_service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
    assert len(family_session.calls) == 1


def test_spa_combined_document_hash_and_evidence_id_change_with_module_bytes(tmp_path: Path):
    shell = _spa_shell()
    hashes = []
    for index, module in enumerate((
        _hmt_company_module(),
        _hmt_company_module().replace(b"2002", b"2003", 1),
    )):
        service = WebArticleArtifactService(
            session=_FakeSession([
                _FakeResponse(shell),
                _FakeResponse(module, headers={"Content-Type": "application/javascript"}),
            ]),
            cache_dir=tmp_path / str(index),
            allowed_hosts=[],
            exact_allowed_hosts=["www.hmtnew.com"],
            resolver=lambda _host: ["93.184.216.34"],
        )
        result = service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
        hashes.append((result["document_hash"], result["evidence_id"]))

    assert hashes[0][0] != hashes[1][0]
    assert hashes[0][1] != hashes[1][1]


@pytest.mark.parametrize(
    "script_src",
    [
        "https://cdn.example.net/company.js",
        "https://assets.www.hmtnew.com/company.js",
    ],
)
def test_spa_rejects_cross_domain_and_child_domain_module_src(tmp_path: Path, script_src: str):
    session = _FakeSession([_FakeResponse(_spa_shell(script_src))])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.net"],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    with pytest.raises(WebArticleArtifactError, match="精确域名|允许的 HTTPS"):
        service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
    assert len(session.calls) == 1


def test_spa_rejects_private_dns_before_module_request(tmp_path: Path):
    resolver_calls = 0

    def resolver(_host):
        nonlocal resolver_calls
        resolver_calls += 1
        return ["93.184.216.34"] if resolver_calls <= 2 else ["127.0.0.1"]

    session = _FakeSession([_FakeResponse(_spa_shell())])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=resolver,
    )

    with pytest.raises(WebArticleArtifactError, match="内网或保留地址"):
        service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
    assert len(session.calls) == 1


@pytest.mark.parametrize("bad_mime", ["text/html", "application/json", "application/octet-stream", ""])
def test_spa_rejects_non_javascript_mime(tmp_path: Path, bad_mime: str):
    session = _FakeSession([
        _FakeResponse(_spa_shell()),
        _FakeResponse(_hmt_company_module(), headers={"Content-Type": bad_mime}),
    ])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    with pytest.raises(WebArticleArtifactError, match="JavaScript MIME"):
        service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )


def test_spa_rejects_cross_host_redirect_and_never_requests_target(tmp_path: Path):
    session = _FakeSession([
        _FakeResponse(_spa_shell()),
        _FakeResponse(status=302, headers={"Location": "https://cdn.example.net/company.js"}),
    ])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.net"],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    with pytest.raises(WebArticleArtifactError, match="跨域重定向"):
        service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
    assert len(session.calls) == 2


def test_spa_reads_only_first_module_and_rejects_code_without_human_text(tmp_path: Path):
    shell = _spa_shell(second_src="/assets/second-company.js")
    code_only = (
        b'const route="/assets/company-profile.js";'
        b'const url="https://www.hmtnew.com/company";'
        b'const css="el-button el-button--primary layout-grid";'
        b'// "\xe5\x8d\x8e\xe6\x87\x8b\xe7\xa7\x91\xe6\x8a\x80\xe8\xbf\x99\xe6\x98\xaf\xe6\xb3\xa8\xe9\x87\x8a\xe4\xb8\xad\xe7\x9a\x84\xe5\x81\x87\xe6\xad\xa3\xe6\x96\x87\xef\xbc\x8c\xe4\xb8\x8d\xe8\x83\xbd\xe8\xa2\xab\xe6\x8f\x90\xe5\x8f\x96"\n'
        b'const tpl=`${name} company profile should never count as static human text`;'
    )
    session = _FakeSession([
        _FakeResponse(shell),
        _FakeResponse(code_only, headers={"Content-Type": "application/javascript"}),
        # A valid second module would succeed if the one-module cap regressed.
        _FakeResponse(_hmt_company_module(), headers={"Content-Type": "application/javascript"}),
    ])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    with pytest.raises(WebArticleArtifactError, match="没有足量"):
        service.fetch_text(
            "https://www.hmtnew.com/",
            allow_same_origin_module_fallback=True,
        )
    assert len(session.calls) == 2


def test_normal_html_never_requests_module_even_when_fallback_is_enabled(tmp_path: Path):
    session = _FakeSession([_FakeResponse(_article_html("华懋科技"))])
    service = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=[],
        exact_allowed_hosts=["www.hmtnew.com"],
        resolver=lambda _host: ["93.184.216.34"],
    )

    result = service.fetch_text(
        "https://www.hmtnew.com/",
        allow_same_origin_module_fallback=True,
    )
    assert result["extraction_method"] == "lxml_readable_text"
    assert "asset_url" not in result
    assert len(session.calls) == 1


class _FakeWebArtifacts:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    @staticmethod
    def can_fetch(url):
        return str(url).startswith("https://www.gov.cn/")

    def fetch_text(self, url):
        self.calls.append(url)
        if self.fail:
            raise WebArticleArtifactError("测试正文抓取失败")
        text = "光模块产业政策与标准正文，包含需求、供给、技术路线和风险核验要求。" * 100
        return {
            "requested_url": url,
            "final_url": url,
            "cached": False,
            "content_type": "text/html; charset=utf-8",
            "document_hash": "document-hash",
            "document_bytes": 4096,
            "text": text,
            "text_hash": "text-hash",
            "text_chars": len(text),
            "title": "光模块产业政策",
            "evidence_id": "web_fulltext:immutable-id",
            "extraction_method": "lxml_readable_text",
        }


def _web_result():
    return {
        "subject": {"name": "光模块", "symbol": None},
        "evidence": [{
            "evidence_id": "web:search-1",
            "kind": "web_policy",
            "source": "国务院",
            "title": "光模块产业政策",
            "summary": "光模块相关政策摘要",
            "date": "2026-08-30",
            "url": "https://www.gov.cn/policy/optical.html",
        }],
        "web_documents": [],
        "source_status": [{"key": "web_search", "status": "covered", "count": 1}],
    }


def _collector_with_web_artifacts(artifacts):
    collector = IndustryResearchSourceCollector.__new__(IndustryResearchSourceCollector)
    collector.web_artifacts = artifacts
    return collector


def test_collector_turns_search_hit_into_auditable_web_fulltext():
    result = _web_result()
    artifacts = _FakeWebArtifacts()
    collector = _collector_with_web_artifacts(artifacts)

    collector._collect_web_fulltext(
        result, topic="光模块", terms=["光通信"], research_type="industry",
    )

    evidence = next(item for item in result["evidence"] if item["kind"] == "web_fulltext")
    assert evidence["evidence_id"] == "web_fulltext:immutable-id"
    assert evidence["document_text_hash"] == "text-hash"
    assert result["web_documents"][0]["document_hash"] == "document-hash"
    status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")
    assert status["status"] == "partial" and status["content_count"] == 1
    assert status["substantive_content_count"] == 1


def test_search_hit_requires_at_least_one_web_body_and_failure_limits_report():
    result = _web_result()
    collector = _collector_with_web_artifacts(_FakeWebArtifacts(fail=True))
    collector._collect_web_fulltext(
        result, topic="光模块", terms=["光通信"], research_type="industry",
    )
    web_status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")
    snapshot = {
        "research_type": "industry",
        "coverage": [{"key": "web_search", "status": "covered", "count": 1}],
        "source_status": result["source_status"],
        "audio_pipeline": {"candidate_count": 0},
    }
    source_plan = IndustryResearchService._source_plan(snapshot)
    web_plan = next(item for item in source_plan if item["key"] == "web_fulltext")

    assert web_status["status"] == "failed" and web_status["count"] == 0
    assert web_plan["required"] is False
    assert web_plan["status"] == "failed" and web_plan["count"] == 0

    quality_snapshot = {
        **snapshot,
        "source_plan": source_plan,
        "research_contract": {"resolved": True},
        "evidence": [{
            "evidence_id": f"fact:{index}", "kind": "web_policy", "source": "测试源",
            "date": "2026-08-30", "url": f"https://example.com/{index}",
            "evidence_level": "factual",
        } for index in range(24)],
        "companies": [{"name": f"公司{index}"} for index in range(3)],
        "concept_context": {"items": [{"name": "光模块"}]},
        "collected_at": "2026-08-31",
        "source_hash": "snapshot-hash",
        "totals": {"evidence": 24},
    }
    quality = IndustryResearchService._assess_data_quality(quality_snapshot)
    assert not any("互联网权威网页正文" in gap for gap in quality["critical_gaps"])
    assert any("互联网网页正文丰富度不足" in warning for warning in quality["warnings"])


def test_non_allowlisted_search_metadata_does_not_create_impossible_fulltext_gate():
    snapshot = {
        "research_type": "industry",
        "coverage": [{"key": "web_search", "status": "covered", "count": 18}],
        "source_status": [
            {"key": "web_search", "status": "covered", "count": 18},
            {
                "key": "web_fulltext", "status": "failed", "count": 0,
                "matched": 18, "eligible": 0, "requested": 0,
                "message": "聚合链接未命中正文白名单",
            },
        ],
        "audio_pipeline": {"candidate_count": 0},
    }

    source_plan = IndustryResearchService._source_plan(snapshot)
    web_plan = next(item for item in source_plan if item["key"] == "web_fulltext")

    assert web_plan["required"] is False
    assert web_plan["metadata_count"] == 18
    assert web_plan["eligible"] == 0


def _company_web_result(website="https://www.hmtnew.com/"):
    return {
        "subject": {
            "name": "华懋科技",
            "symbol": "603306.SH",
            "website": website,
        },
        "evidence": [],
        "web_documents": [],
        "source_status": [{"key": "web_search", "status": "covered", "count": 0}],
    }


def test_company_official_website_gets_task_scoped_exact_host_and_fulltext(tmp_path: Path):
    session = _FakeSession([
        _FakeResponse(_article_html("华懋科技 603306")),
        _FakeResponse(_article_html("华懋科技 603306")),
    ])
    base_artifacts = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["sse.com.cn"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    assert base_artifacts.can_fetch("https://www.hmtnew.com/") is False
    scoped = base_artifacts.with_exact_allowed_hosts(["www.hmtnew.com"])
    assert scoped.can_fetch("https://www.hmtnew.com/") is True
    assert scoped.can_fetch("https://ir.www.hmtnew.com/") is False

    result = _company_web_result()
    collector = _collector_with_web_artifacts(base_artifacts)
    collector._collect_web_fulltext(
        result, topic="华懋科技", terms=["603306"], research_type="company",
    )

    official = next(
        item for item in result["web_documents"]
        if item["requested_url"] == "https://www.hmtnew.com/"
    )
    evidence = next(
        item for item in result["evidence"]
        if item["kind"] == "web_fulltext" and item["requested_url"] == "https://www.hmtnew.com/"
    )
    status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")

    assert official["host"] == "www.hmtnew.com"
    assert official["document_hash"] and official["text_hash"]
    assert official["date"] is None and official["retrieved_at"]
    assert evidence["company"] == "华懋科技"
    assert evidence["date"] == "" and evidence["retrieved_at"]
    assert "公司官网" in evidence["source"]
    assert status["content_count"] == 2
    assert base_artifacts.can_fetch("https://www.hmtnew.com/") is False


def test_company_collector_uses_spa_fallback_and_persists_asset_audit_fields(tmp_path: Path):
    session = _FakeSession([
        _FakeResponse(_spa_shell()),
        _FakeResponse(
            _hmt_company_module(),
            headers={"Content-Type": "application/javascript; charset=utf-8"},
        ),
    ])
    base_artifacts = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.org"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    result = {
        "subject": {
            "name": "华懋科技",
            "symbol": "",
            "website": "https://www.hmtnew.com/",
        },
        "evidence": [],
        "web_documents": [],
        "source_status": [],
    }

    _collector_with_web_artifacts(base_artifacts)._collect_web_fulltext(
        result, topic="华懋科技", terms=["603306"], research_type="company",
    )

    document = result["web_documents"][0]
    evidence = next(item for item in result["evidence"] if item["kind"] == "web_fulltext")
    status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")
    assert document["extraction_method"] == "same_origin_module_static_strings"
    assert document["asset_url"] == "https://www.hmtnew.com/assets/index-company.js"
    assert document["asset_document_hash"] and document["html_document_hash"]
    assert document["document_hash"] not in {
        document["asset_document_hash"], document["html_document_hash"],
    }
    assert evidence["asset_url"] == document["asset_url"]
    assert evidence["asset_document_hash"] == document["asset_document_hash"]
    assert "华懋科技" in evidence["document_text"] and "603306" in evidence["document_text"]
    assert document["content_role"] == "company_profile"
    assert document["satisfies_substantive_fulltext"] is False
    assert status["status"] == "partial" and status["content_count"] == 1
    assert status["company_profile_count"] == 1
    assert status["substantive_content_count"] == 0


def test_company_spa_shell_is_not_coverage_but_exchange_body_can_fallback(tmp_path: Path):
    spa_shell = b"<html><head><title>Company</title></head><body><div id='root'></div><script src='/app.js'></script></body></html>"
    session = _FakeSession([
        _FakeResponse(spa_shell),
        _FakeResponse(_article_html("华懋科技 603306")),
    ])
    artifacts = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["sse.com.cn"],
        resolver=lambda _host: ["93.184.216.34"],
    )
    result = _company_web_result()

    _collector_with_web_artifacts(artifacts)._collect_web_fulltext(
        result, topic="华懋科技", terms=["603306"], research_type="company",
    )

    assert len(result["web_documents"]) == 1
    assert result["web_documents"][0]["host"] == "www.sse.com.cn"
    status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")
    assert status["status"] == "partial"
    assert status["requested"] == 2 and status["content_count"] == 1
    assert any("正文过短" in item.get("error", "") for item in status["failures"])


def test_company_official_website_private_dns_and_literal_private_ip_are_rejected(tmp_path: Path):
    session = _FakeSession([])
    artifacts = WebArticleArtifactService(
        session=session,
        cache_dir=tmp_path,
        allowed_hosts=["example.org"],
        resolver=lambda _host: ["127.0.0.1"],
    )
    result = {
        **_company_web_result(),
        # A non-A-share suffix avoids adding the exchange fallback so this
        # test isolates the exact company website path.
        "subject": {
            "name": "华懋科技",
            "symbol": "",
            "website": "https://www.hmtnew.com/",
        },
    }

    _collector_with_web_artifacts(artifacts)._collect_web_fulltext(
        result, topic="华懋科技", terms=[], research_type="company",
    )

    status = next(item for item in result["source_status"] if item["key"] == "web_fulltext")
    assert session.calls == []
    assert status["status"] == "failed" and status["content_count"] == 0
    assert any("内网或保留地址" in item.get("error", "") for item in status["failures"])
    with pytest.raises(WebArticleArtifactError, match="域名而不是 IP"):
        WebArticleArtifactService.normalize_exact_https_url("https://127.0.0.1/private")


def test_company_aggregator_metadata_without_body_is_required_missing_and_limited():
    snapshot = {
        "research_type": "company",
        "coverage": [{"key": "web_search", "status": "covered", "count": 18}],
        "source_status": [
            {"key": "web_search", "status": "covered", "count": 18},
            {
                "key": "web_fulltext", "status": "failed", "count": 0,
                "matched": 18, "eligible": 0, "requested": 0,
                "message": "Google News 聚合链接没有可安全读取的出版源正文",
            },
        ],
        "audio_pipeline": {"candidate_count": 0},
    }
    source_plan = IndustryResearchService._source_plan(snapshot)
    web_plan = next(item for item in source_plan if item["key"] == "web_fulltext")

    assert web_plan["required"] is False
    assert web_plan["status"] == "failed"
    assert web_plan["metadata_count"] == 18 and web_plan["count"] == 0

    quality = IndustryResearchService._assess_data_quality({
        **snapshot,
        "source_plan": source_plan,
        "research_contract": {"resolved": True},
        "evidence": [],
        "financial_series": [],
        "market_series": [],
        "filing_documents": [],
        "collected_at": "2026-08-31",
        "source_hash": "company-web-gate",
        "totals": {"evidence": 0},
    })
    assert not any("互联网权威网页正文" in gap for gap in quality["critical_gaps"])
    assert any("互联网网页正文丰富度不足" in warning for warning in quality["warnings"])
