#!/usr/bin/env python3
"""Tests for shell scripts: enable-autostart, disable-autostart, pre-commit-hook."""

import os
import sys
import shutil
import tempfile
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILS = []
PASSES = []


def check(desc, condition, detail=""):
    if condition:
        PASSES.append(desc)
    else:
        FAILS.append((desc, detail))


def heading(text):
    print("\n" + "=" * 68)
    print("  " + text)
    print("=" * 68)


def summary():
    total = len(PASSES) + len(FAILS)
    print("\n" + "-" * 68)
    print("  RESULTS: %d / %d pass" % (len(PASSES), total))
    if FAILS:
        print("  FAILURES:")
        for desc, detail in FAILS:
            print("    \u2718 %s" % desc)
            if detail:
                print("      \u2192 %s" % detail)
    else:
        print("  All tests passed.")
    print("-" * 68)
    return len(FAILS)


# ===================================================================
# 1. enable-autostart.sh
# ===================================================================
heading("1. enable-autostart.sh")

BASE = tempfile.mkdtemp()
try:
    # Copy the script + template into a temp dir so we can control the template
    sandbox = os.path.join(BASE, "sandbox")
    os.makedirs(sandbox)
    shutil.copy(os.path.join(SCRIPT_DIR, "enable-autostart.sh"), sandbox)
    template_path = os.path.join(sandbox, "protonvpn-tray.desktop")

    # 1.1 Normal: template exists → copies to $HOME/.config/autostart/
    with open(template_path, "w") as f:
        f.write("[Desktop Entry]\nExec=python3 /old/path/tray.py\n")

    autostart_dir = os.path.join(BASE, ".config", "autostart")
    desktop_file = os.path.join(autostart_dir, "protonvpn-tray.desktop")

    result = subprocess.run(
        [os.path.join(sandbox, "enable-autostart.sh")],
        capture_output=True, text=True, timeout=15,
        cwd=sandbox,
        env={**os.environ, "HOME": BASE},
    )
    check("1.1: exit 0", result.returncode == 0,
          "rc=%d stderr=%s" % (result.returncode, result.stderr[:100]))
    check("1.2: autostart file created", os.path.exists(desktop_file),
          "expected %s" % desktop_file)
    if os.path.exists(desktop_file):
        with open(desktop_file) as f:
            content = f.read()
        check("1.3: Exec path includes --auto-connect",
              "--auto-connect" in content,
              "Exec line: %s" % content[:200])

    # 1.2 Template missing → exits 1
    os.remove(template_path)
    result2 = subprocess.run(
        [os.path.join(sandbox, "enable-autostart.sh")],
        capture_output=True, text=True, timeout=15,
        cwd=sandbox,
        env={**os.environ, "HOME": BASE},
    )
    check("1.4: no template → exit non-zero",
          result2.returncode != 0,
          "rc=%d" % result2.returncode)
    check("1.5: error message contains ERROR",
          "ERROR:" in result2.stdout or "ERROR:" in result2.stderr,
          "stdout: %s" % result2.stdout[:200])

finally:
    shutil.rmtree(BASE, ignore_errors=True)

# ===================================================================
# 2. disable-autostart.sh
# ===================================================================
heading("2. disable-autostart.sh")

BASE2 = tempfile.mkdtemp()
try:
    sandbox2 = os.path.join(BASE2, "sandbox")
    os.makedirs(sandbox2)
    shutil.copy(os.path.join(SCRIPT_DIR, "disable-autostart.sh"), sandbox2)

    autostart_dir = os.path.join(BASE2, ".config", "autostart")
    os.makedirs(autostart_dir, exist_ok=True)
    desktop_file = os.path.join(autostart_dir, "protonvpn-tray.desktop")

    # 2.1 File exists → removed
    with open(desktop_file, "w") as f:
        f.write("[Desktop Entry]\n")
    result = subprocess.run(
        [os.path.join(sandbox2, "disable-autostart.sh")],
        capture_output=True, text=True, timeout=15,
        cwd=sandbox2,
        env={**os.environ, "HOME": BASE2},
        input="n\n",
    )
    check("2.1: exit 0", result.returncode == 0,
          "rc=%d" % result.returncode)
    check("2.2: file removed", not os.path.exists(desktop_file))

    # 2.3 --quiet mode: removes file without prompt
    with open(desktop_file, "w") as f:
        f.write("[Desktop Entry]\n")
    result_quiet = subprocess.run(
        [os.path.join(sandbox2, "disable-autostart.sh"), "--quiet"],
        capture_output=True, text=True, timeout=15,
        cwd=sandbox2,
        env={**os.environ, "HOME": BASE2},
    )
    check("2.3: --quiet exits 0", result_quiet.returncode == 0)
    check("2.4: --quiet removes file", not os.path.exists(desktop_file))

    # 2.5 No file → prints message, no error
    result_nofile = subprocess.run(
        [os.path.join(sandbox2, "disable-autostart.sh"), "--quiet"],
        capture_output=True, text=True, timeout=15,
        cwd=sandbox2,
        env={**os.environ, "HOME": BASE2},
    )
    check("2.5: no file → exit 0", result_nofile.returncode == 0,
          "rc=%d" % result_nofile.returncode)
    check("2.6: message about not enabled",
          "not enabled" in (result_nofile.stdout + result_nofile.stderr).lower(),
          "stdout: %s" % result_nofile.stdout[:200])

finally:
    shutil.rmtree(BASE2, ignore_errors=True)

# ===================================================================
# 3. pre-commit-hook — git integration
# ===================================================================
heading("3. pre-commit-hook")

HOOK_PATH = os.path.join(SCRIPT_DIR, "pre-commit-hook")


def create_repo():
    root = tempfile.mkdtemp()
    subprocess.run(["git", "init"], capture_output=True, text=True,
                   timeout=10, cwd=root)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   capture_output=True, text=True, timeout=10, cwd=root)
    subprocess.run(["git", "config", "user.name", "Test"],
                   capture_output=True, text=True, timeout=10, cwd=root)
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", root],
                   capture_output=True, text=True, timeout=5)
    # Make initial commit so staging has context
    readme = os.path.join(root, "README.md")
    with open(readme, "w") as f:
        f.write("initial\n")
    subprocess.run(["git", "add", "README.md"], capture_output=True, text=True,
                   timeout=10, cwd=root)
    subprocess.run(["git", "commit", "-m", "init"], capture_output=True, text=True,
                   timeout=10, cwd=root)
    return root


def stage_and_scan(repo, filename, content,
                   env_override=None, skip_scan=False):
    filepath = os.path.join(repo, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w") as f:
        f.write(content)
    subprocess.run(["git", "add", filepath], capture_output=True, text=True,
                   timeout=10, cwd=repo)
    env = {**os.environ, "USER": "tester", "HOSTNAME": "tester-host"}
    if skip_scan:
        env["SKIP_SECRET_SCAN"] = "1"
    if env_override:
        env.update(env_override)
    return subprocess.run([HOOK_PATH], capture_output=True, text=True,
                          timeout=30, cwd=repo, env=env)


# 3.1 No staged files → exits 0
repo = create_repo()
# Remove README from staging so nothing is staged
subprocess.run(["git", "rm", "--cached", "README.md"], capture_output=True,
               text=True, timeout=10, cwd=repo)
result = subprocess.run([HOOK_PATH], capture_output=True, text=True,
                        timeout=15, cwd=repo,
                        env={**os.environ, "SKIP_SECRET_SCAN": ""})
check("3.1: no staged files → exit 0", result.returncode == 0,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.2 Clean file → exits 0
repo = create_repo()
result = stage_and_scan(repo, "hello.py", "print('hello')\n")
check("3.2: clean file → exit 0", result.returncode == 0,
      "rc=%d stderr=%s" % (result.returncode, result.stderr[:200]))
shutil.rmtree(repo, ignore_errors=True)

# 3.3 Private key → blocked
repo = create_repo()
result = stage_and_scan(repo, "key.pem",
                        "-----BEGIN PRIVATE KEY-----\nABCDEF\n-----END PRIVATE KEY-----\n")
check("3.3: private key → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.4 AWS API key → blocked
repo = create_repo()
result = stage_and_scan(repo, "creds.txt",
                        "aws_access_key = AKIAIOSFODNN7EXAMPLE\n")
check("3.4: AWS key → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)
print("  STDOUT for 3.4:", result.stdout[:200] if result.stdout else "(empty)", file=sys.stderr)

# 3.5 GitHub token → blocked
repo = create_repo()
result = stage_and_scan(repo, "config.yml",
                        "token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
check("3.5: GitHub token → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.6 Credential assignment in JSON → blocked
repo = create_repo()
result = stage_and_scan(repo, "settings.json",
                        '{"api_key": "TESTKEY_live_xxxxxxxxxxxxxxxxxxxxxxxx"}\n')
check("3.6: API key pattern → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.7 docs/ file with token → SKIPPED (should exit 0)
repo = create_repo()
result = stage_and_scan(repo, "docs/readme.md",
                        "token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n")
check("3.7: docs/ skips scan → exit 0", result.returncode == 0,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.8 .sh file with secret → blocked
repo = create_repo()
result = stage_and_scan(repo, "deploy.sh",
                        'api_key="TESTKEY_test_xxxxxxxxxxxxxxxxxxxxxxxx"\n')
check("3.8: .sh scanned → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.9 MAC address detection
repo = create_repo()
result = stage_and_scan(repo, "network.txt",
                        "Some MAC: 12:34:56:78:9a:bc\n",
                        env_override={"USER": "tester", "HOSTNAME": "testhost"})
check("3.9: MAC address → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.10 UUID detection
repo = create_repo()
result = stage_and_scan(repo, "uids.txt",
                        "found uuid 550e8400-e29b-41d4-a716-446655440000\n",
                        env_override={"USER": "tester", "HOSTNAME": "testhost"})
check("3.10: UUID → exit 1", result.returncode == 1,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# 3.11 SKIP_SECRET_SCAN=1 → hook exits 0 without scanning
repo = create_repo()
result = stage_and_scan(repo, "secret.py",
                        "key = 'TESTKEY_test_xxxxxxxxxxxxxxxxxxxxxxxx'\n",
                        skip_scan=True)
check("3.11: SKIP_SECRET_SCAN=1 → exit 0", result.returncode == 0,
      "rc=%d" % result.returncode)
shutil.rmtree(repo, ignore_errors=True)

# ===================================================================
# Summary
# ===================================================================
sys.exit(summary())
