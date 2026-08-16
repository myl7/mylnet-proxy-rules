# mylnet-proxy-rules

Web editor for the mihomo extend rules consumed by the mylnet Ansible setup.

The site reads and writes `clash_rules_extend.yaml` in the
`mylnet-ansible-secrets` repository and, on "Save and apply", commits the change
there and re-runs the subscription playbook so the proxy subscription on `sg`
serves the new rules. nginx in front of the app does the authentication
(`auth_basic`); the app itself has none.

## The file format coupling

The subscription template
(`mylnet-ansible/playbooks/templates/proxy/clash.yaml.j2`) reads the rules file
as raw text:

```jinja
{{ lookup('ansible.builtin.file', 'secrets/clash_rules_extend.yaml').split('\n')[1:] | join('\n') }}
```

It drops the first line (`rules:`) and pastes the rest into the surrounding
`rules:` list. The editor must therefore always write exactly

```
rules:
  - TYPE,payload,target[,options][ # note]
```

with two-space indentation. `app/rules.py` enforces this byte for byte, and the
round-trip tests in `tests/test_rules.py` pin it down. If the template's
injection ever changes, update the serializer here to match.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/meta` | Allowed rule types, targets, limits |
| GET | `/api/rules` | Rules plus the file's sha256 as a revision |
| POST | `/api/apply` | Validate and start a write-and-apply job (`dry_run` for check-only) |
| GET | `/api/jobs/{id}` | Job state and log, polled by the frontend |
| GET | `/healthz` | Health check |

Validation happens server-side before anything is written: known types only
(`MATCH` and the logic types are excluded on purpose), payload shape per type,
target and option whitelists, no duplicates, and a cap on the rule count. The
apply pipeline is single-slot: the write is atomic (temp file + rename), the
previous content is backed up, a `--check` run happens first, and a failed
apply restores the file and commits a revert. Git push failures are warnings
only, because the rules are already live by then.

## Local development

```sh
make dev        # uvicorn with reload on :8000
make test       # unit tests
make lint       # ruff + mypy
make format     # prettier + ruff fix
```

The defaults in `app/config.py` assume the deployed layout; override everything
with `MYLNET_*` environment variables.

## Deployment

`mylnet-ansible/playbooks/mylnet-proxy-rules.yaml` deploys the site onto `sg`:
it builds the container image, starts it with the two Ansible repositories bind
mounted at `/srv/mylnet`, and renders the nginx site (with `auth_basic`, served
at `prules.myl.moe`) in front of it. The container runs the same paths inside,
so the relative `playbooks/secrets` symlink in the Ansible repo keeps
resolving.

Prerequisites on `sg` (manual, involve keys):

- `/srv/mylnet/mylnet-ansible` and `/srv/mylnet/mylnet-ansible-secrets` as
  sibling clones, with git-crypt unlocked
- `/srv/mylnet/mylnet-proxy-rules` as a clone of this repository (the image
  build context)
- the container's ssh key in `/srv/mylnet/.ssh`, whose public key is in
  `myl`'s `authorized_keys` on `sg`, with passwordless sudo for `myl`

The playbook asserts all of this before touching anything. The apply jobs run
`ansible-playbook playbooks/proxy.yaml --tags proxy-sub --limit sg` inside the
container, which is why the `Serve proxy subscription` play carries the
`proxy-sub` tag.
