"""
PyPI Publishing Assistant for jobagent
Automates packaging verification and uploading to PyPI / TestPyPI.
"""
import os
import sys
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

def main():
    console = Console()
    console.print(Panel.fit(
        "[bold cyan]======================================================[/bold cyan]\n"
        "[bold white]           JobAgent PyPI Release Assistant            [/bold white]\n"
        "[dim]  Build, Verify & Upload Distribution to https://pypi.org [/dim]\n"
        "[bold cyan]======================================================[/bold cyan]",
        border_style="cyan"
    ))

    dist_dir = Path("dist")
    import re
    with open("pyproject.toml", "r", encoding="utf-8") as f:
        m = re.search(r'version\s*=\s*"([^"]+)"', f.read())
        cur_version = m.group(1) if m else "1.0.32"

    whl_files = list(dist_dir.glob(f"jobagent-{cur_version}*.whl"))
    tar_files = list(dist_dir.glob(f"jobagent-{cur_version}*.tar.gz"))

    if not whl_files or not tar_files:
        console.print(f"[yellow]Distribution archives for version {cur_version} not found in dist/. Building now...[/yellow]")
        res = subprocess.run([sys.executable, "-m", "build"], capture_output=False)
        if res.returncode != 0:
            console.print("[bold red][ERROR] Package build failed![/bold red]")
            sys.exit(1)
        whl_files = list(dist_dir.glob(f"jobagent-{cur_version}*.whl"))
        tar_files = list(dist_dir.glob(f"jobagent-{cur_version}*.tar.gz"))

    console.print(f"\n[bold green][OK] Found Distribution Packages for version {cur_version} in dist/:[/bold green]")
    for f in whl_files + tar_files:
        console.print(f"  [cyan]•[/cyan] {f.name} ({f.stat().st_size / 1024:.1f} KB)")

    # 1. Twine Check
    console.print("\n[dim]Running twine check verification...[/dim]")
    check_res = subprocess.run([sys.executable, "-m", "twine", "check", f"dist/jobagent-{cur_version}*"], capture_output=True, text=True)
    if "PASSED" in check_res.stdout:
        console.print("[bold green][OK] Twine metadata and README check passed![/bold green]")
    else:
        console.print(f"[yellow]{check_res.stdout}[/yellow]")

    # 2. Target Repository Selection
    console.print("\n[bold yellow]Select PyPI Target Repository:[/bold yellow]")
    console.print("  [1] 🚀 Production PyPI (https://pypi.org/) -> Makes 'pip install jobagent' public")
    console.print("  [2] 🧪 TestPyPI (https://test.pypi.org/) -> Safe test run before production")
    choice = Prompt.ask("Choose target", choices=["1", "2"], default="1")
    is_test = (choice == "2")

    # 3. Authentication Guidance
    target_name = "TestPyPI" if is_test else "Production PyPI"
    token_url = "https://test.pypi.org/manage/account/token/" if is_test else "https://pypi.org/manage/account/token/"
    
    console.print(Panel(
        f"[bold white]Authentication for {target_name}[/bold white]\n\n"
        f"1. Open your browser and go to: [bold cyan]{token_url}[/bold cyan]\n"
        "2. Generate an API Token with Scope: [bold]Entire account (all projects)[/bold] or [bold]Project: jobagent[/bold]\n"
        "3. Copy the token starting with [bold yellow]pypi-...[/bold yellow]\n"
        "[dim]Note: PyPI strictly requires API tokens (password uploads are disabled).[/dim]",
        border_style="yellow"
    ))

    env_token = os.environ.get("PYPI_API_TOKEN") or os.environ.get("TWINE_PASSWORD")
    if env_token:
        console.print("[bold green]Found API token in environment variables![/bold green]")
        token = env_token
    else:
        token = Prompt.ask("Paste your PyPI API Token (starts with pypi-...)", password=True)

    if not token or not token.strip().startswith("pypi-"):
        console.print("[bold red][ERROR] Invalid token format. PyPI tokens must start with 'pypi-'.[/bold red]")
        sys.exit(1)

    token = token.strip()
    upload_cmd = [
        sys.executable, "-m", "twine", "upload",
        "--skip-existing",
        "--username", "__token__",
        "--password", token,
    ]
    if is_test:
        upload_cmd.extend(["--repository-url", "https://test.pypi.org/legacy/"])

    upload_cmd.extend([str(f) for f in whl_files + tar_files])

    console.print(f"\n[bold cyan]Uploading to {target_name}...[/bold cyan]")
    result = subprocess.run(upload_cmd)

    if result.returncode == 0:
        console.print(Panel.fit(
            f"[bold green]🎉 SUCCESS! jobagent was successfully published to {target_name}![/bold green]\n\n"
            f"[white]Anyone can now install your agent with:[/white]\n"
            f"[bold cyan]pip install jobagent[/bold cyan]\n\n"
            f"[dim]And run it anywhere with:[/dim]\n"
            f"[bold yellow]jobagent[/bold yellow]",
            border_style="green"
        ))
    else:
        console.print(f"[bold red]Upload returned non-zero code {result.returncode}. Please check the error message above.[/bold red]")

if __name__ == "__main__":
    main()
