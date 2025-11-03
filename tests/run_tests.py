#!/usr/bin/env python3
"""
Test runner script for the weather application.
Can be run with: python run_tests.py
"""

import sys
import subprocess
import os


def run_tests():
    """Run all tests for the weather application."""
    print("🌪️  Running Weather Application Tests")
    print("=" * 50)
    
    # Change to the parent directory to ensure proper module resolution
    original_dir = os.getcwd()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    os.chdir(parent_dir)
    
    try:
        # Check if pytest is available, fallback to unittest
        try:
            import pytest
            print("Using pytest...")
            
            # Run tests with pytest - specify tests directory
            cmd = [
                sys.executable, "-m", "pytest", 
                "tests/",
                "-v",
                "--tb=short"
            ]
            
            # Add coverage if pytest-cov is available
            try:
                import pytest_cov
                cmd.extend(["--cov=weather", "--cov-report=term-missing"])
            except ImportError:
                print("Note: pytest-cov not available, running without coverage")
            
            result = subprocess.run(cmd)
            
        except ImportError:
            print("pytest not found, using unittest...")
            
            # Run tests with unittest - run from tests directory
            cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
            result = subprocess.run(cmd)
        
        print("\n" + "=" * 50)
        if result.returncode == 0:
            print("✅ All tests passed!")
        else:
            print("❌ Some tests failed!")
            
    finally:
        # Restore original directory
        os.chdir(original_dir)
        
    return result.returncode


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
