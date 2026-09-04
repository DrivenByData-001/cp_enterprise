import subprocess

result = subprocess.run(
    [".venv\\Scripts\\python.exe", "-m", "scripts.bootstrap_vocabulary", "--dry-run"],
    capture_output=True,
    text=True,
    timeout=60,
)
print("STDOUT:")
print(result.stdout, end="")
print("STDERR:")
print(result.stderr, end="")
