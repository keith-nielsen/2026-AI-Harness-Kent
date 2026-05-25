# Kent — Developer Notes

Operational notes for developing and testing the Kent stack.

## Run Modes

The installer supports three model configurations via flags:

| Flag | Config file | Models | Use case |
|------|-------------|--------|----------|
| `--dev` | `litellm_config.dev.yaml` | Small local (gemma3:1b, qwen3:1.7b, qwen3:4b) | Basic install testing on limited VRAM |
| `--cloud` | `litellm_config.cloud.yaml` | All tiers via DeepSeek V4 Flash | Flow testing when local models can't handle context |
| *(neither)* | `litellm_config.yaml` | Production local (gemma4:4b, gemma4:26b, qwen3.6:27b) | Full production deployment |

### Cloud mode

```bash
sudo ./resetinstall.sh --include-ollama --full-purge
sudo ./install.sh --cloud
```

Routes all four logical tiers (router, fast, smart, frontier) to DeepSeek V4 Flash
via the DeepSeek API. Requires `DEEPSEEK_API_KEY` in the environment or in
`/home/kent/secrets/`.

This mode exists because the local dev models (gemma3:4b and similar) cannot
reliably handle the context sizes that Hermes + CrewAI require. Specifically:

- SOUL.md alone works on gemma3:4b with `--toolsets none`
- Adding skills (crew-designer) or enabling toolsets causes context overflow
  or incoherent responses on small models
- CrewAI multi-agent flows need models that can hold agent backstories + task
  context + tool schemas simultaneously

Cloud mode skips pulling large local models (saves disk and time) but still
pulls gemma3:1b for Ollama health checks.

DeepSeek V4 Flash costs roughly $0.11/$0.22 per 1M input/output tokens
with 1M context window. A full crew-designer flow test costs fractions of a cent.

### Switching modes

The model config is deployed to `/etc/litellm/config.yaml` during phase 3. To
switch modes without a full reinstall:

```bash
# Switch to cloud mode
sudo cp /path/to/kent-repo/configs/litellm_config.cloud.yaml /etc/litellm/config.yaml
sudo systemctl restart litellm-gateway

# Switch back to dev
sudo cp /path/to/kent-repo/configs/litellm_config.dev.yaml /etc/litellm/config.yaml
sudo systemctl restart litellm-gateway
```

## Testing the Crew Designer Skill

After install, verify the skill deployed:

```bash
ls /home/kent/.hermes/skills/kent/crew-designer/SKILL.md
ls /home/kent/.hermes/SOUL.md
```

Test that Kent sees the skill:

```bash
kent chat --toolsets skills -q "What skills do you have?"
```

Test the crew design flow:

```bash
kent chat --toolsets skills -q "I need a crew to research competitor pricing in SaaS"
```

If running with `--toolsets none` (e.g. on dev hardware), Kent won't load
skills. Either pass `--toolsets skills` explicitly or set the default:

```bash
kent config set toolsets.default '["skills"]'
```

## Known Limitations

- **gemma3:4b**: Can handle SOUL.md context alone. Fails with skills loaded
  or toolsets enabled. Use `--toolsets none` and smart tier only.
- **qwen3:1.7b/4b**: Similar context limitations. The nothink variants help
  with response quality but don't fix context overflow.
- **CrewAI context**: A 3-agent sequential crew with tool schemas needs roughly
  8-12K tokens of system context before the task even starts. Models with less
  than 16K effective context will struggle.
