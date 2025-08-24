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
    
    # Check if pytest is available, fallback to unittest
    try:
        import pytest
        print("Using pytest...")
        
        # Run tests with pytest
        cmd = [
            sys.executable, "-m", "pytest", 
            "test_weather.py", 
            "test_integration.py",
            "test_env_support.py",
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
        
        # Run tests with unittest
        cmd = [sys.executable, "-m", "unittest", "discover", "-v"]
        result = subprocess.run(cmd)
    
    print("\n" + "=" * 50)
    if result.returncode == 0:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed!")
        
    return result.returncode


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
