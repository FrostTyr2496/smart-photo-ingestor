"""CLI interface for the Photo Ingest Tool."""

import click
import sys
from pathlib import Path
from typing import Optional

from .analyzer import PhotoAnalyzer
from .output_formatter import AnalysisFormatter
from .output_manager import OutputManager
from .config import FileTypes, PerformanceConfig, ConfigManager, ConfigurationError


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
@click.option('--peek', is_flag=True, help='Include visual content analysis using LLM')
@click.option('--exif-only', is_flag=True, help='Skip visual analysis even if LLM configured')
@click.option('--samples', default=5, help='Number of sample images per device for visual analysis')
@click.option('--detailed/--basic', default=True, help='Detailed EXIF analysis (default) or basic analysis')
@click.option('--summary', is_flag=True, help='Show only a brief summary')
@click.option('--workers', default=4, help='Number of parallel workers for processing')
@click.pass_context
def analyze(ctx, folder: Path, config: Optional[Path], json: bool, peek: bool, exif_only: bool, samples: int, detailed: bool, summary: bool, workers: int):
    """Analyze folder contents with optional visual content analysis."""
    verbose = ctx.obj.get('verbose', 0)
    quiet = ctx.obj.get('quiet', False)
    
    # Validate peek options
    if peek and exif_only:
        click.echo("Error: Cannot specify both --peek and --exif-only", err=True)
        ctx.exit(1)
    
    if not quiet:
        click.echo(f"📁 Analyzing folder: {folder}")
        if peek:
            click.echo(f"👁️  Visual analysis enabled (sampling {samples} images per device)")
        elif exif_only:
            click.echo("📊 EXIF-only analysis mode")
        
        if detailed:
            click.echo("🔍 Using detailed EXIF analysis")
        else:
            click.echo("⚡ Using basic EXIF analysis")
    
    try:
        # Initialize output manager
        output_manager = OutputManager(verbosity=verbose, quiet=quiet)
        
        # Load configuration if provided (for peek mode)
        ingest_config = None
        if config or peek:
            try:
                ingest_config = ConfigManager.load_config(config)
                if peek and not ingest_config.llm.enabled:
                    output_manager.print_warning("Peek mode requested but LLM is not enabled in config")
                    if not exif_only:
                        output_manager.print_message("Falling back to EXIF-only analysis")
                        peek = False
            except ConfigurationError as e:
                if peek and not exif_only:
                    output_manager.print_warning(f"Config error: {e}")
                    output_manager.print_message("Falling back to EXIF-only analysis")
                    peek = False
                # For analyze command, config is optional, so continue without it
        
        # Configure analyzer
        file_types = ingest_config.file_types if ingest_config else FileTypes()
        performance_config = ingest_config.performance if ingest_config else PerformanceConfig(parallel_workers=workers)
        analyzer = PhotoAnalyzer(file_types, performance_config, detailed=detailed)
        
        # Enhanced progress callback
        def progress_callback(phase, current, total, message):
            if not quiet:
                if phase == "scan":
                    output_manager.print_progress(f"🔍 Scanning: {current} files found...", style="cyan")
                elif phase == "exif":
                    percentage = (current / total) * 100 if total > 0 else 0
                    output_manager.print_progress(f"📸 Extracting EXIF: {current}/{total} ({percentage:.1f}%) - {message}", style="green")
        
        # Run analysis
        result = analyzer.analyze_directory(
            folder, 
            progress_callback=progress_callback if not quiet else None
        )
        
        if not quiet:
            click.echo()  # New line after progress
        
        # TODO: Implement peek mode visual analysis here
        if peek and not exif_only:
            output_manager.print_message("🔮 Visual analysis not yet implemented", style="yellow")
        
        # Display results using output manager
        output_manager.display_analyze_results(result, json_format=json)
        
        if result.total_files == 0:
            output_manager.print_error("No supported photo files found in the specified directory.")
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