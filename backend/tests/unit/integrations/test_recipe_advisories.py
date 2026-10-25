from oh_goon.integrations.recipe_advisories import extract_security_findings


def test_native_uv_audit_dependency_identity_survives_finding_projection() -> None:
    # Field shape and advisory identities captured from actual uv 0.13.3 audit
    # against requests 2.19.1; native uv/OSV retain vulnerability interpretation.
    native_summary = (
        "Internationalized Domain Names in Applications (IDNA) vulnerable to denial of service "
        "from specially crafted inputs to idna.encode"
    )
    payload = {
        "schema": {"version": "preview"},
        "summary": {"audited_packages": 5, "vulnerabilities": 2, "adverse_statuses": 0},
        "vulnerabilities": [
            {
                "dependency": {"name": "idna", "version": "2.7"},
                "id": "GHSA-jjg7-2v4v-x38h",
                "display_id": "PYSEC-2024-60",
                "aliases": ["CVE-2024-3651", "PYSEC-2024-60"],
                "summary": native_summary,
                "fix_versions": ["3.7"],
            },
            {
                "dependency": {"name": "idna", "version": "2.7"},
                "id": "PYSEC-2024-60",
                "display_id": "PYSEC-2024-60",
                "aliases": ["CVE-2024-3651", "GHSA-jjg7-2v4v-x38h"],
                "summary": None,
                "fix_versions": ["3.7"],
            },
        ],
    }
    findings = extract_security_findings(payload)
    assert [item.advisory_id for item in findings] == ["GHSA-jjg7-2v4v-x38h", "PYSEC-2024-60"]
    assert all(item.package == "idna" and item.version == "2.7" for item in findings)
    assert all(item.fixed_versions == ("3.7",) for item in findings)
    assert findings[0].aliases == ("CVE-2024-3651", "PYSEC-2024-60")
    assert findings[0].summary == native_summary
    assert findings[1].summary is None


def test_native_uv_finding_rows_keep_each_dependency_and_version() -> None:
    findings = extract_security_findings(
        {
            "vulnerabilities": [
                {
                    "dependency": {"name": "first-package", "version": "1.0"},
                    "id": "GHSA-shared-id",
                    "aliases": [],
                    "fix_versions": [],
                },
                {
                    "dependency": {"name": "second-package", "version": "2.0"},
                    "id": "GHSA-shared-id",
                    "aliases": [],
                    "fix_versions": [],
                },
            ]
        }
    )
    assert [(item.package, item.version) for item in findings] == [
        ("first-package", "1.0"),
        ("second-package", "2.0"),
    ]
