#!/usr/bin/env python3
"""
Spec-Driven Design - Coherence Validator

Validates that specs in a project's /specs directory follow the SDD templates
and checks for basic coherence issues between documents.

Usage:
    python validate_specs.py /path/to/project/specs
"""

import os
import sys
import re
from pathlib import Path


def find_spec_files(specs_dir: str) -> dict:
    """Find spec files organized by type."""
    specs = {"prd": [], "api": [], "technical": [], "data-model": [], "plans": []}
    base = Path(specs_dir)
    
    if not base.exists():
        print(f"ERROR: Directory {specs_dir} does not exist")
        sys.exit(1)
    
    for subdir, files_list in specs.items():
        subpath = base / subdir
        if subpath.exists():
            for f in subpath.glob("*.md"):
                files_list.append(f)
    
    # Also check root level
    for f in base.glob("*.md"):
        content = f.read_text(encoding="utf-8", errors="replace")
        if "Product Requirements Document" in content:
            specs["prd"].append(f)
        elif "API Specification" in content:
            specs["api"].append(f)
        elif "Technical Design Document" in content:
            specs["technical"].append(f)
        elif "Data Model Specification" in content:
            specs["data-model"].append(f)
        elif "Implementation Plan" in content:
            specs["plans"].append(f)
    
    return specs


def check_required_sections(filepath: Path, required_sections: list[str]) -> list[str]:
    """Check that a file contains required sections."""
    content = filepath.read_text(encoding="utf-8", errors="replace")
    issues = []
    for section in required_sections:
        if section.lower() not in content.lower():
            issues.append(f"Missing section: '{section}'")
    return issues


def check_prd(filepath: Path) -> list[str]:
    """Validate a PRD file."""
    sections = [
        "Resumen Ejecutivo", "Contexto y Problema", "Usuarios Objetivo",
        "Objetivos y Métricas", "Alcance", "Requisitos Funcionales",
        "Requisitos No Funcionales"
    ]
    issues = check_required_sections(filepath, sections)
    
    content = filepath.read_text(encoding="utf-8", errors="replace")
    
    # Check for Out of Scope
    if "out of scope" not in content.lower():
        issues.append("WARNING: No 'Out of Scope' section found - risk of scope creep")
    
    # Check for verifiable requirements
    rf_pattern = re.findall(r"RF-\d+", content)
    if not rf_pattern:
        issues.append("WARNING: No RF-XXX requirements found")
    
    # Check for MoSCoW priorities
    if not any(p in content for p in ["MUST", "SHOULD", "COULD", "WONT"]):
        issues.append("WARNING: No MoSCoW priorities found in requirements")
    
    return issues


def check_api_spec(filepath: Path) -> list[str]:
    """Validate an API Spec file."""
    sections = [
        "Autenticación", "Convenciones Generales", "Endpoints",
        "Formato de Error", "Rate Limiting"
    ]
    issues = check_required_sections(filepath, sections)
    
    content = filepath.read_text(encoding="utf-8", errors="replace")
    
    # Check for HTTP methods
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    found_methods = [m for m in methods if f"`{m} " in content or f"### " in content and m in content]
    if not found_methods:
        issues.append("WARNING: No HTTP endpoints documented")
    
    # Check for error codes
    if "VALIDATION_ERROR" not in content and "error" not in content.lower():
        issues.append("WARNING: No error codes documented")
    
    return issues


def check_data_model(filepath: Path) -> list[str]:
    """Validate a Data Model file."""
    sections = ["Schema", "Índices", "Queries Críticas"]
    issues = check_required_sections(filepath, sections)
    
    content = filepath.read_text(encoding="utf-8", errors="replace")
    
    # Check for Decimal128 in financial contexts
    if "amount" in content.lower() and "Decimal128" not in content and "decimal" not in content.lower():
        issues.append("CRITICAL: Financial amounts found without Decimal128 - precision risk")
    
    # Check for float warning
    if "float" in content.lower() and "amount" in content.lower():
        issues.append("CRITICAL: Float type used for financial amounts - use Decimal128")
    
    return issues


def validate_specs(specs_dir: str):
    """Main validation function."""
    specs = find_spec_files(specs_dir)
    
    total_issues = 0
    
    print("=" * 60)
    print("Spec-Driven Design - Coherence Validator")
    print("=" * 60)
    print()
    
    # Report found specs
    for spec_type, files in specs.items():
        count = len(files)
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {spec_type}: {count} file(s)")
    print()
    
    # Validate each type
    validators = {
        "prd": check_prd,
        "api": check_api_spec,
        "data-model": check_data_model,
    }
    
    for spec_type, files in specs.items():
        if not files:
            continue
        
        validator = validators.get(spec_type)
        if not validator:
            continue
        
        for filepath in files:
            issues = validator(filepath)
            if issues:
                print(f"[{spec_type.upper()}] {filepath.name}:")
                for issue in issues:
                    print(f"  - {issue}")
                    total_issues += 1
                print()
    
    # Summary
    print("-" * 60)
    if total_issues == 0:
        print("✓ All specs passed validation")
    else:
        print(f"Found {total_issues} issue(s) to review")
    
    return total_issues


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_specs.py /path/to/specs")
        sys.exit(1)
    
    issues = validate_specs(sys.argv[1])
    sys.exit(1 if issues > 0 else 0)
