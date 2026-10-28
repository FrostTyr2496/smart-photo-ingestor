"""CLI interface for the Photo Ingest Tool."""

import click
import sys
from pathlib import Path
from typing import Optional

from .analyzer import PhotoAnalyzer
from .analyze_command import AnalyzeCommand, EnhancedAnalysisResult
from .visual_analyzer import VisualContentAnalyzer
from .output_formatter import AnalysisFormatter
from .output_manager import OutputManager
from .config import FileTypes, PerformanceConfig, ConfigManager, ConfigurationError, PeekConfig, LLMConfig
from .exceptions import PhotoIngestError, format_user_error
from .settings import settings_manager


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
@click.option('--folder-analysis/--no-folder-analysis', default=True, help='Enable folder-by-folder analysis (default: enabled)')
@click.option('--location/--no-location', default=True, help='Enable GPS location resolution (default: enabled)')
@click.pass_context
def analyze(ctx, folder: Path, config: Optional[Path], json: bool, peek: bool, exif_only: bool, samples: int, detailed: bool, summary: bool, workers: int, folder_analysis: bool, location: bool):
    """Analyze folder contents with optional visual content analysis."""
    verbose = ctx.obj.get('verbose', 0)
    quiet = ctx.obj.get('quiet', False)
    
    # Validate peek options
    if peek and exif_only:
        click.echo("Error: Cannot specify both --peek and --exif-only", err=True)
        ctx.exit(1)
    
    if not quiet and not json:
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
        
        # Load configuration - try provided config, then saved config, then defaults
        ingest_config = None
        config_source = None
        
        # Determine config file to use
        config_to_use = config
        if not config_to_use:
            # Try to use saved config path
            saved_config = settings_manager.get_config_path()
            if saved_config and Path(saved_config).exists():
                config_to_use = saved_config
                config_source = "saved"
        
        if config_to_use:
            try:
                ingest_config = ConfigManager.load_config(config_to_use)
                
                # Save the config path if it was explicitly provided
                if config and config != settings_manager.get_config_path():
                    settings_manager.set_config_path(config)
                    if not quiet and not json:
                        output_manager.print_message(f"💾 Saved config path: {config}")
                elif config_source == "saved" and not quiet and not json:
                    output_manager.print_message(f"📁 Using saved config: {config_to_use}")
                
                if peek and not ingest_config.llm.enabled:
                    output_manager.print_warning("Peek mode requested but LLM is not enabled in config")
                    if not exif_only:
                        output_manager.print_message("Falling back to EXIF-only analysis")
                        peek = False
                        
            except ConfigurationError as e:
                output_manager.print_warning(f"Config error: {e}")
                if peek and not exif_only:
                    output_manager.print_message("Falling back to EXIF-only analysis")
                    peek = False
                # For analyze command, config is optional, so continue without it
        elif peek and not exif_only:
            # Peek mode requested but no config provided or found
            output_manager.print_warning("Peek mode requested but no configuration file provided")
            output_manager.print_message("💡 Tip: Use --config to specify a config file, or run 'photo-ingest config set' to save one")
            output_manager.print_message("Falling back to EXIF-only analysis")
            peek = False
        
        # Configure enhanced analyzer
        file_types = ingest_config.file_types if ingest_config else FileTypes()
        performance_config = ingest_config.performance if ingest_config else PerformanceConfig(parallel_workers=workers)
        
        # Create enhanced analyze command
        analyze_command = AnalyzeCommand(
            config=ingest_config,
            file_types=file_types,
            performance_config=performance_config
        )
        
        # Enable verbose logging if requested
        if verbose > 0:
            import logging
            logging.basicConfig(
                level=logging.INFO if verbose == 1 else logging.DEBUG,
                format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            analyze_command.set_verbose(True)
        
        # Configure location services
        if analyze_command.visual_analyzer and not location:
            # Recreate visual analyzer with location disabled
            analyze_command.visual_analyzer = VisualContentAnalyzer(
                ingest_config.peek if ingest_config else PeekConfig(),
                ingest_config.llm if ingest_config else LLMConfig(),
                verbose=verbose > 0,
                enable_location=False
            )
        
        # Enhanced progress callback
        def progress_callback(phase, current, total, message):
            if not quiet:
                if phase == "scan":
                    output_manager.print_progress(f"🔍 Scanning: {current} files found...", style="cyan")
                elif phase == "exif":
                    percentage = (current / total) * 100 if total > 0 else 0
                    output_manager.print_progress(f"📸 Extracting EXIF: {current}/{total} ({percentage:.1f}%) - {message}", style="green")
                elif phase == "visual":
                    percentage = (current / total) * 100 if total > 0 else 0
                    output_manager.print_progress(f"👁️  Visual Analysis: {current}/{total} ({percentage:.1f}%) - {message}", style="magenta")
                elif phase == "analyze":
                    percentage = (current / total) * 100 if total > 0 else 0
                    output_manager.print_progress(f"🔍 Analysis: {message} ({percentage:.0f}%)", style="blue")
        
        # Run enhanced analysis
        result = analyze_command.analyze_directory(
            folder,
            peek_mode=peek,
            exif_only=exif_only,
            samples=samples,
            enable_folder_analysis=folder_analysis,
            progress_callback=progress_callback if not (quiet or json) else None
        )
        
        if not quiet and not json:
            click.echo()  # New line after progress
        
        # Display results using enhanced output manager
        output_manager.display_enhanced_analyze_results(result, json_format=json)
        
        if result.exif_analysis.total_files == 0:
            output_manager.print_error("No supported photo files found in the specified directory.")
            ctx.exit(1)
        
    except ImportError as e:
        click.echo(f"❌ Missing dependency: {e}", err=True)
        click.echo("Install required packages with: pip install Pillow", err=True)
        ctx.exit(1)
    except PhotoIngestError as e:
        click.echo(f"❌ {format_user_error(e)}", err=True)
        ctx.exit(1)
    except Exception as e:
        click.echo(f"❌ Analysis failed: {format_user_error(e)}", err=True)
        if verbose > 1:
            import traceback
            traceback.print_exc()
        ctx.exit(1)


@main.group()
def config():
    """Manage configuration settings."""
    pass


@config.command()
@click.argument('config_file', type=click.Path(exists=True, path_type=Path))
def set(config_file: Path):
    """Set the default configuration file path."""
    try:
        # Validate the config file by trying to load it
        ConfigManager.load_config(config_file)
        
        # Save the config path
        settings_manager.set_config_path(str(config_file))
        
        click.echo(f"✅ Default config set to: {config_file.resolve()}")
        click.echo("💡 You can now use --peek without --config flag")
        
    except ConfigurationError as e:
        click.echo(f"❌ Invalid config file: {e}", err=True)
        click.echo("Please check your configuration file and try again.", err=True)


@config.command()
def show():
    """Show current configuration settings."""
    saved_config = settings_manager.get_config_path()
    
    if saved_config:
        config_path = Path(saved_config)
        if config_path.exists():
            click.echo(f"📁 Default config: {saved_config}")
            click.echo(f"✅ Status: Found")
            
            # Show some basic config info
            try:
                ingest_config = ConfigManager.load_config(saved_config)
                click.echo(f"🔧 LLM enabled: {ingest_config.llm.enabled}")
                click.echo(f"👁️  Visual analysis: {ingest_config.peek.include_visual_analysis}")
                click.echo(f"📦 Archive root: {ingest_config.archive_root}")
            except ConfigurationError:
                click.echo("⚠️  Config file has errors")
        else:
            click.echo(f"📁 Default config: {saved_config}")
            click.echo(f"❌ Status: File not found")
    else:
        click.echo("📁 No default config set")
        click.echo("💡 Use 'photo-ingest config set <file>' to set one")


@config.command()
def clear():
    """Clear the saved configuration file path."""
    settings_manager.clear_config_path()
    click.echo("✅ Default config cleared")
    click.echo("💡 You'll need to use --config flag or set a new default")


@config.command()
@click.argument('output_file', type=click.Path(path_type=Path))
def create(output_file: Path):
    """Create an example configuration file."""
    try:
        ConfigManager.create_example_config(output_file)
        click.echo(f"✅ Example config created: {output_file.resolve()}")
        click.echo("💡 Edit the file to match your setup, then use 'photo-ingest config set' to make it default")
    except Exception as e:
        click.echo(f"❌ Failed to create config: {e}", err=True)


@main.command()
@click.option('--config', type=click.Path(path_type=Path), help='Configuration file path')
@click.option('--source', required=True, type=click.Path(exists=True, path_type=Path), help='Source directory to ingest')
@click.option('--event', required=True, help='Event name for organization')
@click.option('--dry-run', is_flag=True, help='Preview operations without executing them')
@click.option('--copy/--move', default=True, help='Copy files (default) or move them')
@click.option('--raw-only', is_flag=True, help='Perform only raw backup')
@click.option('--organized-only', is_flag=True, help='Perform only organized import')
@click.option('--json', 'json_output', is_flag=True, help='Output results in JSON format')
@click.pass_context
def ingest(ctx, config: Optional[Path], source: Path, event: str, dry_run: bool,
           copy: bool, raw_only: bool, organized_only: bool, json_output: bool):
    """Ingest photos into structured archive with optional raw backup."""
    from .ingest_command import IngestCommand
    import json as _json

    verbose = ctx.obj.get('verbose', 0)
    quiet = ctx.obj.get('quiet', False)

    if raw_only and organized_only:
        click.echo("Error: Cannot specify both --raw-only and --organized-only", err=True)
        ctx.exit(1)
        return

    if not quiet and not json_output:
        click.echo(f"📥 Ingesting from: {source}")
        click.echo(f"🏷️  Event: {event}")
        click.echo(f"📋 Mode: {'Copy' if copy else 'Move'}")
        if raw_only:
            click.echo("📦 Raw backup only")
        elif organized_only:
            click.echo("📂 Organised import only")
        else:
            click.echo("🔄 Both raw backup and organised import")
        if dry_run:
            click.echo("🔍 DRY RUN — no files will be modified")

    # Load config
    output_manager = OutputManager(verbosity=verbose, quiet=quiet)
    ingest_config = None

    config_to_use = config
    if not config_to_use:
        # Order: --config -> ./ingest.yaml -> ~/.photo-ingest.yaml -> saved default
        discovered = ConfigManager._resolve_config_path(None)
        if discovered.exists():
            config_to_use = discovered
    if not config_to_use:
        saved = settings_manager.get_config_path()
        if saved and Path(saved).exists():
            config_to_use = saved

    if config_to_use:
        try:
            ingest_config = ConfigManager.load_config(config_to_use)
        except ConfigurationError as e:
            click.echo(f"❌ Config error: {e}", err=True)
            ctx.exit(1)
            return
    else:
        click.echo("❌ No configuration file found. Use --config or run 'photo-ingest config set'.", err=True)
        ctx.exit(1)
        return

    # Progress callback
    def progress_callback(phase, current, total, message):
        if quiet:
            return
        pct = f"{current/total*100:.0f}%" if total else ""
        icons = {"scan": "🔍", "exif": "📸", "dedup": "🔄", "copy": "📂"}
        icon = icons.get(phase, "⚙️")
        output_manager.print_progress(f"{icon} {phase.capitalize()}: {message} {pct}")

    try:
        cmd = IngestCommand(ingest_config, progress_callback=progress_callback if not (quiet or json_output) else None)
        result = cmd.run(
            source=source,
            event_name=event,
            copy_mode=copy,
            dry_run=dry_run,
            raw_only=raw_only,
            organized_only=organized_only,
        )

        if not quiet and not json_output:
            click.echo()  # newline after progress

        if json_output:
            import dataclasses
            print(_json.dumps(dataclasses.asdict(result), indent=2, default=str))
        else:
            _display_ingest_result(result, output_manager, dry_run)

        if result.errors:
            ctx.exit(1)

    except (click.exceptions.Exit, click.exceptions.Abort):
        # ctx.exit()/abort must propagate; don't treat them as pipeline failures.
        raise
    except Exception as e:
        click.echo(f"❌ Ingest failed: {format_user_error(e)}", err=True)
        if verbose > 1:
            import traceback
            traceback.print_exc()
        ctx.exit(1)


def _display_ingest_result(result, output_manager: OutputManager, dry_run: bool) -> None:
    """Pretty-print ingest results."""
    prefix = "🔍 DRY RUN" if dry_run else "✅ Ingest"
    output_manager.print_message(f"\n{prefix} complete in {result.processing_time_seconds:.1f}s")
    output_manager.print_message(f"  📁 Total files:    {result.total_files:,}")
    output_manager.print_message(f"  ✅ Copied/moved:   {result.files_copied + result.files_moved:,}")
    output_manager.print_message(f"  🔄 Duplicates:     {result.duplicates_found:,}")
    output_manager.print_message(f"  ⏭️  Skipped:        {result.files_skipped:,}")
    if result.manifest_path:
        output_manager.print_message(f"  📄 Manifest:       {result.manifest_path}")
    if result.summary_path:
        output_manager.print_message(f"  📝 Summary:        {result.summary_path}")
    if result.notes_path:
        output_manager.print_message(f"  🤖 Notes:          {result.notes_path}")
    if result.errors:
        output_manager.print_warning(f"  ⚠️  {len(result.errors)} error(s) — check logs")


if __name__ == '__main__':
    main()