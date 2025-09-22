"""Tests for CLI interface."""

import pytest
from click.testing import CliRunner
from pathlib import Path
import tempfile
import os

from photo_ingest.cli import main


class TestCLI:
    """Test CLI interface functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        
    def teardown_method(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_main_help(self):
        """Test main command help."""
        result = self.runner.invoke(main, ['--help'])
        assert result.exit_code == 0
        assert 'Photo Ingest Tool' in result.output
        assert 'analyze' in result.output
        assert 'ingest' in result.output
    
    def test_analyze_help(self):
        """Test analyze command help."""
        result = self.runner.invoke(main, ['analyze', '--help'])
        assert result.exit_code == 0
        assert 'Analyze folder contents' in result.output
        assert '--peek' in result.output
        assert '--json' in result.output
    
    def test_ingest_help(self):
        """Test ingest command help."""
        result = self.runner.invoke(main, ['ingest', '--help'])
        assert result.exit_code == 0
        assert 'Ingest photos into structured archive' in result.output
        assert '--source' in result.output
        assert '--event' in result.output
        assert '--dry-run' in result.output
    
    def test_analyze_basic(self):
        """Test basic analyze command."""
        result = self.runner.invoke(main, ['analyze', self.temp_dir])
        assert result.exit_code == 0
        assert f'Analyzing folder: {self.temp_dir}' in result.output
    
    def test_analyze_with_options(self):
        """Test analyze command with options."""
        result = self.runner.invoke(main, [
            'analyze', self.temp_dir, 
            '--peek', '--samples', '3', '--json'
        ])
        assert result.exit_code == 0
        assert 'Visual analysis enabled with 3 samples' in result.output
        assert 'JSON output format selected' in result.output
    
    def test_ingest_missing_required_options(self):
        """Test ingest command with missing required options."""
        result = self.runner.invoke(main, ['ingest'])
        assert result.exit_code != 0
        assert 'Missing option' in result.output
    
    def test_ingest_conflicting_options(self):
        """Test ingest command with conflicting options."""
        result = self.runner.invoke(main, [
            'ingest', 
            '--source', self.temp_dir,
            '--event', 'test',
            '--raw-only',
            '--organized-only'
        ])
        assert result.exit_code == 1
        assert 'Cannot specify both --raw-only and --organized-only' in result.output
    
    def test_ingest_basic(self):
        """Test basic ingest command."""
        result = self.runner.invoke(main, [
            'ingest',
            '--source', self.temp_dir,
            '--event', 'Test Event'
        ])
        assert result.exit_code == 0
        assert f'Ingesting from: {self.temp_dir}' in result.output
        assert 'Event: Test Event' in result.output
        assert 'Mode: Copy' in result.output
    
    def test_ingest_with_options(self):
        """Test ingest command with various options."""
        result = self.runner.invoke(main, [
            'ingest',
            '--source', self.temp_dir,
            '--event', 'Test Event',
            '--move',
            '--dry-run',
            '--raw-only'
        ])
        assert result.exit_code == 0
        assert 'Mode: Move' in result.output
        assert 'DRY RUN MODE' in result.output
        assert 'Raw backup only mode' in result.output
    
    def test_verbosity_options(self):
        """Test verbosity options."""
        result = self.runner.invoke(main, ['-v', '-v', 'analyze', self.temp_dir])
        assert result.exit_code == 0
        
        result = self.runner.invoke(main, ['--quiet', 'analyze', self.temp_dir])
        assert result.exit_code == 0