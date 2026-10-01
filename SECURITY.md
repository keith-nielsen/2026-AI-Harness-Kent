# Security Policy

## Supported Versions

Kent is at the v0.1.0 release-candidate stage. Security fixes go to `main` and
the latest release candidate, and are announced in GitHub Releases.

| Version | Supported |
|---------|-----------|
| main | ✅ Active development |
| v0.1.0-rc.x | ✅ Latest release candidate |

Kent is a reference implementation, not a certified system. The controls in
place, and the gaps to close before processing regulated data, are listed in
[`docs/controls.md`](docs/controls.md).

## Reporting a Vulnerability

**Please do not file public issues for security vulnerabilities.**

Kent uses **GitHub Private Vulnerability Reporting** — the preferred
channel for security disclosures.

To report a vulnerability:

1. Go to the repository's **Security** tab
2. Click **Report a vulnerability**
3. Fill in the description and impact details

GitHub will notify the maintainer directly. No public email address is
exposed, and the report remains confidential until resolved.

### What to include

- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact
- Any suggested mitigation (optional)

## Response Timeline

- **Acknowledgment:** Within 48 hours of submission
- **Initial assessment:** Within 5 business days
- **Fix timeline:** Communicated based on severity

## Disclosure Policy

We follow coordinated disclosure. The reporter will be credited in the
release notes once the fix is published (unless anonymity is requested).

## Scope

The following are in scope:

- Kent agent code, Gent runtime and templates
- Install modules (`install/services/`) and their defaults
- The gateway access policy (`kent_gateway.py`) and egress proxy rules
- Configuration defaults
- Documentation that, if followed, introduces a security risk

The following are out of scope:

- Third-party dependencies (report to their respective maintainers)
- The physical security of deployment hardware