"""CLI interface for the Photo Ingest Tool."""

import click
from pathlib import Path
from typing import Optional


@click.group()
@click.option('--verbose', '-v', count=True, help='Increase verbosity (use multiple times)')
@click.option('--quiet', '-q', is_flag=True, help='Suppress non-essential output')
@click.pass_context
def main(ctx, verbose: int, quiet: bool):
    """Photo Ingest Tool for organizing photography imports."""
    # Ensure context object exists
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    ctx.obj['quiet'] = quiet


@main.command()
@click.argument('folder', type=click.Path(exists=True, path_type=Path))
@click.option('--config', type=click.Path(path_type=Path), help='Configuration file path')
@click.option('--json', is_flag=True, help='Output results in JSON format')
@click.option('--peek', is_flag=True, help='Include visual content analysis')
@click.option('--exif-only', is_flag=True, help='Skip visual analysis even if LLM configured')
@click.option('--samples', default=5, help='Number of sample images per device for visual analysis')
@click.pass_context
def analyze(ctx, folder: Path, config: Optional[Path], json: bool, peek: bool, exif_only: bool, samples: int):
    """Analyze folder contents with optional visual content analysis."""
    click.echo(f"Analyzing folder: {folder}")
    if peek and not exif_only:
        click.echo(f"Visual analysis enabled with {samples} samples per device")
    if json:
        click.echo("JSON output format selected")
    # TODO: Implement analyze functionality


@main.command()
@click.option('--config', type=click.Path(path_type=Path), help='Configuration file path')
@click.option('--source', required=True, type=click.Path(exists=True, path_type=Path), help='Source directory to ingest')
@click.option('--event', required=True, help='Event name for organization')
@click.option('--dry-run', is_flag=True, help='Preview operations without executing them')
@click.option('--copy/--move', default=True, help='Copy files (default) or move them')
@click.option('--raw-only', is_flag=True, help='Perform only raw backup')
@click.option('--organized-only', is_flag=True, help='Perform only organized import')
@click.pass_context
def ingest(ctx, config: Optional[Path], source: Path, event: str, dry_run: bool, copy: bool, raw_only: bool, organized_only: bool):
    """Ingest photos into structured archive with optional raw backup."""
    click.echo(f"Ingesting from: {source}")
    click.echo(f"Event: {event}")
    click.echo(f"Mode: {'Copy' if copy else 'Move'}")
    
    if raw_only and organized_only:
        click.echo("Error: Cannot specify both --raw-only and --organized-only", err=True)
        ctx.exit(1)
    
    if raw_only:
        click.echo("Raw backup only mode")
    elif organized_only:
        click.echo("Organized import only mode")
    else:
        click.echo("Both raw backup and organized import")
    
    if dry_run:
        click.echo("DRY RUN MODE - No files will be modified")
    
    # TODO: Implement ingest functionality


if __name__ == '__main__':
    main()