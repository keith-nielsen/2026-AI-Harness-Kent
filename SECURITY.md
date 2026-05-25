# Security Policy

## Supported Versions

Kent is in pre-alpha development. No stable release has been published yet.
Security patches will be applied to the main branch and communicated via
GitHub Releases once available.

| Version | Supported |
|---------|-----------|
| main (bleeding edge) | ✅ Active development |
| v0.1.0-prealpha | Future |

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

- Kent agent code and templates
- Installation scripts
- Configuration defaults
- Documentation that, if followed, introduces a security risk

The following are out of scope:

- Third-party dependencies (report to their respective maintainers)
- The physical security of deployment hardware