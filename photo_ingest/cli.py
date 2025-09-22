"""CLI interface for the Photo Ingest Tool."""

import click
import sys
from pathlib import Path
from typing import Optional

from .analyzer import PhotoAnalyzer
from .output_formatter import AnalysisFormatter
from .config import FileTypes, PerformanceConfig


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
@click.option('--detailed/--basic', default=True, help='Detailed EXIF analysis (default) or basic analysis')
@click.option('--summary', is_flag=True, help='Show only a brief summary')
@click.option('--workers', default=4, help='Number of parallel workers for processing')
@click.pass_context
def analyze(ctx, folder: Path, config: Optional[Path], json: bool, detailed: bool, summary: bool, workers: int):
    """Analyze folder contents with comprehensive EXIF metadata extraction."""
    verbose = ctx.obj.get('verbose', 0)
    quiet = ctx.obj.get('quiet', False)
    
    if not quiet:
        click.echo(f"📁 Analyzing folder: {folder}")
        if detailed:
            click.echo("🔍 Using detailed EXIF analysis")
        else:
            click.echo("⚡ Using basic EXIF analysis")
    
    try:
        # Configure analyzer
        file_types = FileTypes()
        performance_config = PerformanceConfig(parallel_workers=workers)
        analyzer = PhotoAnalyzer(file_types, performance_config, detailed=detailed)
        
        # Enhanced progress callback
        def progress_callback(phase, current, total, message):
            if not quiet:
                if phase == "scan":
                    click.echo(f"\r🔍 Scanning: {current} files found...", nl=False)
                elif phase == "exif":
                    percentage = (current / total) * 100 if total > 0 else 0
                    click.echo(f"\r📸 Extracting EXIF: {current}/{total} ({percentage:.1f}%) - {message}", nl=False)
        
        # Run analysis
        result = analyzer.analyze_directory(
            folder, 
            progress_callback=progress_callback if not quiet else None
        )
        
        if not quiet:
            click.echo()  # New line after progress
        
        # Format and output results
        if json:
            output = AnalysisFormatter.format_json(result)
        elif summary:
            output = AnalysisFormatter.format_summary(result)
        else:
            output = AnalysisFormatter.format_human_readable(result, detailed=detailed)
        
        click.echo(output)
        
        if result.total_files == 0:
            click.echo("No supported photo files found in the specified directory.", err=True)
            ctx.exit(1)
        
    except ImportError as e:
        click.echo(f"❌ Missing dependency: {e}", err=True)
        click.echo("Install required packages with: pip install Pillow", err=True)
        ctx.exit(1)
    except Exception as e:
        click.echo(f"❌ Analysis failed: {e}", err=True)
        if verbose > 1:
            import traceback
            traceback.print_exc()
        ctx.exit(1)


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