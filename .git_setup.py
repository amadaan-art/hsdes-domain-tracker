import subprocess, sys

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print("STDERR:", r.stderr.strip())
    return r.returncode

print("=== git add -A ===")
run(['git', 'add', '-A'])

print("\n=== git status --short ===")
run(['git', 'status', '--short'])

print("\n=== commit ===")
code = run(['git', 'commit', '-m',
    'Update README, requirements, gitignore; add SampleResult; rename from HSDES_DomainTrack to hsdes-domain-tracker'])
if code != 0:
    print("Commit failed or nothing to commit")

print("\n=== update remote URL ===")
run(['git', 'remote', 'set-url', 'origin',
     'https://github.com/Amadaan-art/hsdes-domain-tracker.git'])
run(['git', 'remote', '-v'])
