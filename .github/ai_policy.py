"""Repository-local merge gate; no cross-repository credentials required."""
import argparse
import json
import pathlib
import re
import subprocess

PRIVATE_FILES = {"credentials.json", "service-account.json", "id_rsa", "id_ed25519"}
PRIVATE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
GENERATED_DIRS = {"node_modules", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".wrangler"}
REVIEW_DIRS = {"infra", "migrations", "terraform"}
REVIEW_FILES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "shopify.app.toml",
                "shopify.extension.toml", "wrangler.json", "wrangler.jsonc", "wrangler.toml"}
SECRET_RULES = (
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\bgh[pousr]_[A-Za-z0-9_]{36,}\b",
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b",
    r"\b[rs]k_live_[A-Za-z0-9]{20,}\b",
)
ASSIGNMENT = re.compile(r'''(?i)\b(?:api_key|access_token|client_secret|password|refresh_token|key)\b[\s"']*[:=]\s*["'][^"'\r\n]{16,}["']''')
EXAMPLES = ("${{", "secrets.", "process.env", "os.environ", "getenv(", "example", "placeholder", "dummy", "redacted")


def inspect_path(name, root):
    path = pathlib.PurePosixPath(name)
    file = pathlib.Path(root, name)
    lower = path.name.lower()
    errors = []
    env_file = lower.startswith(".env") and lower not in {".env.example", ".env.sample", ".env.template"}
    if env_file or lower in PRIVATE_FILES or path.suffix.lower() in PRIVATE_SUFFIXES:
        errors.append("private credential file")
    if GENERATED_DIRS.intersection(path.parts):
        errors.append("generated dependency/cache directory")
    if file.is_file():
        if file.stat().st_size > 10 * 1024 * 1024:
            errors.append("file exceeds 10 MiB")
        if path.suffix.lower() == ".json":
            try:
                json.loads(file.read_text(encoding="utf-8"))
            except (ValueError, UnicodeError) as error:
                errors.append(f"invalid JSON ({type(error).__name__})")
    sensitive = (
        name.startswith((".github/workflows/", ".github/actions/"))
        or name == ".github/ai_policy.py"
        or bool(REVIEW_DIRS.intersection(path.parts))
        or lower in REVIEW_FILES
        or path.suffix.lower() in {".tf", ".tfvars", ".service", ".timer"}
        or lower.startswith("alchemy.")
        or ("deploy" in lower and path.suffix.lower() in {".js", ".mjs", ".py", ".sh", ".ts"})
        or ("schema" in lower and path.suffix.lower() in {".js", ".json", ".py", ".sql", ".ts"})
    )
    return errors, sensitive


def contains_secret(line):
    if any(re.search(pattern, line) for pattern in SECRET_RULES):
        return True
    return bool(ASSIGNMENT.search(line)) and not any(marker in line.lower() for marker in EXAMPLES)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    comparison = f"{args.base}...HEAD"
    names = subprocess.check_output(["git", "diff", "--name-only", "-z", "--diff-filter=ACMRTUXB", comparison]).decode().split("\0")
    failed, review = False, False
    for name in filter(None, names):
        errors, sensitive = inspect_path(name, ".")
        review |= sensitive
        for error in errors:
            print(f"Rejected {name}: {error}")
        if sensitive:
            print(f"Explicit review required: {name}")
        failed |= bool(errors)
    whitespace = subprocess.run(["git", "diff", "--check", comparison], capture_output=True)
    if whitespace.returncode:
        print("Rejected whitespace errors; run git diff --check locally.")
        failed = True
    diff = subprocess.check_output(["git", "diff", "--no-ext-diff", "--unified=0", comparison]).decode(errors="replace")
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++") and contains_secret(line[1:]):
            print("Rejected potential credential in added text (value redacted).")
            failed = True
    if args.output:
        with open(args.output, "a", encoding="utf-8") as output:
            output.write(f"requires_review={str(review).lower()}\n")
    print(f"Repository policy: {'FAIL' if failed else 'PASS'}; explicit review: {review}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
