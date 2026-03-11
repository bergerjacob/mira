# Archived Test Scripts

These scripts represent **deprecated approaches** that were tested but not adopted for production.

## Why Archived?

- **Obsolete strategies**: We chose Plan+Constraint over these approaches
- **Superseded**: Functionality merged into newer scripts
- **Reference only**: Keep for historical context, not for active use

## Files

| Script | Original Purpose | Why Archived |
|--------|-----------------|--------------|
| `benchmark.py` | Full benchmark with server | Requires server, obsolete approach |
| `comprehensive_circuit_test.py` | Circuit testing | Redundant with `ultra_comprehensive_test.py` |
| `test_generation_strategy.py` | Strategy comparison | Findings in `docs/PAST_ATTEMPTS.md` |
| `test_reasoning_traces.py` | Reasoning trace tests | Findings documented |
| `test_iterative_generation.py` | Iterative (1 block/call) | We chose Plan+Constraint instead |
| `test_iterative_simple.py` | Simple iterative test | Same as above |
| `test_reasoning_quality.py` | Reasoning quality analysis | Findings in `docs/TESTING_REPORT.md` |
| `qualitative_analysis.py` | Output analysis | Redundant with `manual_inspection.py` |
| `qualitative_inspection.py` | Plan inspection | Redundant with `manual_inspection.py` |

## Active Scripts

See parent directory for current test scripts:
- `test_api.py` - API connectivity
- `quick_test.py` - Fast quality test
- `test_complex_circuits_strategies.py` - Main validation
- `test_batch_strategies.py` - Strategy comparison
- `ultra_comprehensive_test.py` - Comprehensive suite
- `manual_inspection.py` - Qualitative verification
- `analyze_results.py` - Post-processing

---

*Archived March 11, 2026*
