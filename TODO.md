# Kent — Development TODO

> Near-term work items for the Kent enterprise agentic AI stack.
> See [docs/roadmap.md](docs/roadmap.md) for the broader vision.

**Status:** Pre-alpha / Architecture phase

## Immediate

- [ ] Write `docs/installation.md` — standalone prose install guide beyond the scripts
- [ ] Validate E2E — run `sudo ./install.sh --cloud` and verify all 8 phase test gates pass
- [ ] First Gent spawn — spawn a Gent from template, verify CEO agent connects to LiteLLM gateway and completes a task

## Short-term

- [ ] Knowledge sharing — verify cross-pollination: Gent A publishes a learning, Gent B inherits it
- [ ] Seed-from archive — archive a Gent, spawn a new one with seed-from, verify domain knowledge transfer
- [ ] Production hardening — validate all entries in the privilege map, run container isolation tests, verify HMAC chain integrity

## Medium-term

- [ ] Open for public collaboration — update CONTRIBUTING.md, tag v0.1.0-prealpha release
- [ ] Quick-start Docker Compose — cloud-only mode so anyone can try the architecture in 2 commands without a GPU

---

*Items move to GitHub Issues when concrete enough to assign. Last updated: 2026-05-26*