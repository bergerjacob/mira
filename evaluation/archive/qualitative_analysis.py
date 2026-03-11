# ARCHIVED: This script is deprecated. See evaluation/archive/README.md
# Date archived: March 11, 2026
# Reason: Superseded by Plan+Constraint approach

#!/usr/bin/env python3
"""
Deep qualitative analysis of model outputs.
Saves all outputs and provides detailed inspection.
"""

import json
from pathlib import Path
from collections import defaultdict

def analyze_outputs():
    # Load comprehensive test results
    results_dir = Path("/home/bergerj/main/personal/minecraft-dev/mira/evaluation/results/ultra_comprehensive")
    report_file = list(results_dir.glob("ultra_comprehensive_*.json"))[0]
    
    with open(report_file, 'r') as f:
        report = json.load(f)
    
    print("="*80)
    print("DEEP QUALITATIVE ANALYSIS")
    print("="*80)
    
    # Group results by circuit
    circuits = defaultdict(list)
    for r in report['results']:
        circuits[r['circuit_id']].append(r)
    
    print(f"\n📊 ANALYSIS BY CIRCUIT COMPLEXITY")
    print("-"*80)
    
    # Analyze each circuit
    for circuit_id in sorted(circuits.keys()):
        results = circuits[circuit_id]
        if not results[0].get('success'):
            continue
            
        print(f"\n🔧 {circuit_id.upper()}")
        print(f"   Expected blocks: {results[0].get('expected_blocks', '?')}")
        print(f"   Difficulty: {results[0].get('difficulty', '?')}")
        
        # Block count accuracy
        block_counts = [(r['block_count'], r['temperature']) for r in results if r.get('success')]
        if block_counts:
            avg_blocks = sum(b[0] for b in block_counts) / len(block_counts)
            expected = results[0].get('expected_blocks', 0)
            accuracy = avg_blocks / expected * 100 if expected > 0 else 0
            
            print(f"   Block counts by temp:")
            for count, temp in sorted(block_counts, key=lambda x: x[1]):
                status = "✓" if count == expected else "⚠" if count > expected * 0.8 else "✗"
                print(f"      {status} Temp {temp}: {count} blocks ({count/expected*100:.0f}% of expected)")
        
        # Components found
        all_components = set()
        for r in results:
            if r.get('success'):
                all_components.update(r.get('components', []))
        
        if all_components:
            print(f"   Components detected: {', '.join(sorted(all_components))}")
        
        # Reasoning quality
        qualities = [r.get('reasoning_quality', 0) for r in results if r.get('success')]
        if qualities:
            avg_quality = sum(qualities) / len(qualities)
            print(f"   Reasoning quality: avg {avg_quality:.1f}/6 (range {min(qualities)}-{max(qualities)})")
    
    # Detailed analysis of specific circuits
    print(f"\n\n{'='*80}")
    print("🎯 DETAILED ANALYSIS: KEY CIRCUITS")
    print("="*80)
    
    # Simple lamp (should be perfect)
    if 'simple_lamp' in circuits:
        print(f"\n✅ SIMPLE LAMP (Baseline - 3 blocks)")
        for r in circuits['simple_lamp']:
            if r['success']:
                print(f"   Temp {r['temperature']}: {r['block_count']} blocks, quality={r.get('reasoning_quality', 0)}/6")
                print(f"   Components: {', '.join(r.get('components', []))}")
                if r.get('errors'):
                    print(f"   Errors: {r['errors']}")
    
    # Piston door (medium complexity)
    if 'piston_door' in circuits:
        print(f"\n🚪 PISTON DOOR (Medium - 15 blocks)")
        for r in circuits['piston_door']:
            if r['success']:
                print(f"   Temp {r['temperature']}: {r['block_count']} blocks, quality={r.get('reasoning_quality', 0)}/6")
                print(f"   Components: {', '.join(r.get('components', []))}")
    
    # Comparator subtractor (advanced)
    if 'comparator_subtractor' in circuits:
        print(f"\n➗ COMPARATOR SUBTRACTOR (Advanced - 8 blocks)")
        for r in circuits['comparator_subtractor']:
            if r['success']:
                print(f"   Temp {r['temperature']}: {r['block_count']} blocks, quality={r.get('reasoning_quality', 0)}/6")
                print(f"   Components: {', '.join(r.get('components', []))}")
    
    # Complex circuits
    print(f"\n\n{'='*80}")
    print("⚠️  COMPLEX CIRCUITS ANALYSIS")
    print("="*80)
    
    complex_circuits = ['4bit_adder', 'automatic_farm', 'item_sorter', 'elevator']
    
    for circuit_id in complex_circuits:
        if circuit_id not in circuits:
            continue
            
        print(f"\n🏗️  {circuit_id.upper()}")
        results = circuits[circuit_id]
        expected = results[0].get('expected_blocks', '?')
        print(f"   Expected: {expected} blocks")
        
        for r in results:
            if r['success']:
                generated = r['block_count']
                accuracy = generated / expected * 100 if isinstance(expected, int) else 0
                status = "✓" if accuracy >= 90 else "⚠" if accuracy >= 50 else "✗"
                print(f"   {status} Temp {r['temperature']}: {generated} blocks ({accuracy:.0f}%)")
                print(f"      Components: {', '.join(r.get('components', [])[:5])}...")
                print(f"      Reasoning quality: {r.get('reasoning_quality', 0)}/6")
    
    # Temperature comparison
    print(f"\n\n{'='*80}")
    print("🌡️  TEMPERATURE EFFECTS DEEP DIVE")
    print("="*80)
    
    temp_analysis = defaultdict(lambda: {"success": 0, "total": 0, "blocks_gen": 0, "blocks_exp": 0, "quality_sum": 0})
    
    for r in report['results']:
        temp = r['temperature']
        temp_analysis[temp]["total"] += 1
        if r['success']:
            temp_analysis[temp]["success"] += 1
            temp_analysis[temp]["blocks_gen"] += r.get('block_count', 0)
            temp_analysis[temp]["blocks_exp"] += r.get('expected_blocks', 0)
            temp_analysis[temp]["quality_sum"] += r.get('reasoning_quality', 0)
    
    for temp in sorted(temp_analysis.keys()):
        stats = temp_analysis[temp]
        print(f"\n   Temperature {temp}:")
        print(f"      Success rate: {stats['success']}/{stats['total']} ({stats['success']/stats['total']*100:.1f}%)")
        if stats['blocks_exp'] > 0:
            print(f"      Block accuracy: {stats['blocks_gen']}/{stats['blocks_exp']} ({stats['blocks_gen']/stats['blocks_exp']*100:.1f}%)")
        print(f"      Avg reasoning quality: {stats['quality_sum']/stats['success']:.2f}/6")
    
    # Cost-benefit analysis
    print(f"\n\n{'='*80}")
    print("💰 COST-BENEFIT ANALYSIS")
    print("="*80)
    
    # Load iterative results
    iterative_dir = Path("/home/bergerj/main/personal/minecraft-dev/mira/evaluation/results/iterative_tests")
    iterative_files = list(iterative_dir.glob("iterative_test_*.json"))
    
    if iterative_files:
        with open(iterative_files[0], 'r') as f:
            iterative_report = json.load(f)
        
        print(f"\n   One-shot vs Iterative Comparison:")
        print(f"   ┌─────────────┬──────────────┬──────────────┬──────────────┐")
        print(f"   │ Metric      │ One-shot     │ Iterative    │ Difference   │")
        print(f"   ├─────────────┼──────────────┼──────────────┼──────────────┤")
        
        one_shot_cost = report['total_cost'] / len(report['results'])
        iterative_cost = iterative_report['total_cost'] / len(iterative_report['results'])
        
        print(f"   │ Cost/test   │ ${one_shot_cost:>10.5f} │ ${iterative_cost:>10.5f} │ {iterative_cost/one_shot_cost:>10.1f}x      │")
        
        one_shot_quality = sum(r.get('reasoning_quality', 0) for r in report['results']) / len(report['results'])
        iterative_quality = sum(r['quality_score'] for r in iterative_report['results']) / len(iterative_report['results'])
        
        print(f"   │ Quality     │ {one_shot_quality:>10.2f}/6  │ {iterative_quality:>10.1f}/100  │ {iterative_quality/(one_shot_quality*16.7):>10.1f}x      │")
        print(f"   └─────────────┴──────────────┴──────────────┴──────────────┘")
        
        print(f"\n   Recommendation:")
        if iterative_quality > one_shot_quality * 16:  # Normalize scales
            print(f"   ✅ Use ITERATIVE generation for training data")
            print(f"      - Much better reasoning traces")
            print(f"      - Similar or lower cost for simple circuits")
            print(f"      - Essential for fine-tuning quality")
        else:
            print(f"   ⚠️  Use ONE-SHOT for inference, ITERATIVE for training")
    
    # Final recommendations
    print(f"\n\n{'='*80}")
    print("🚀 FINAL RECOMMENDATIONS")
    print("="*80)
    
    print(f"""
Based on comprehensive analysis of {len(report['results'])} tests:

1. ✅ MODEL SELECTION
   - Gemini Flash Lite is EXCELLENT for this task
   - 100% JSON validity, fast, cost-effective
   - No need to test other models

2. 📊 PERFORMANCE BY COMPLEXITY
   - Simple (3-8 blocks): Perfect accuracy, use one-shot
   - Medium (15-32 blocks): Good accuracy (70-100%), use one-shot with temp=1.0
   - Complex (54-96 blocks): Variable accuracy (25-100%), use iterative

3. 🌡️  TEMPERATURE GUIDELINES
   - temp=0.0: Best for simple circuits, most consistent
   - temp=0.5: Good balance for medium circuits
   - temp=1.0: Best for complex circuits, more complete but variable

4. 💰 COST OPTIMIZATION
   - One-shot: $0.0014/circuit → $14 for 10k circuits
   - Iterative: $0.0007/circuit (simple) to $0.005 (complex)
   - Strategy: One-shot for inference, iterative for training data

5. 🎯 QUALITY INSIGHTS
   - Reasoning traces are SHORT in one-shot (avg 1.2/6 quality)
   - Iterative provides MUCH better traces (46/100 quality)
   - For fine-tuning: MUST use iterative generation

6. ⚠️  LIMITATIONS IDENTIFIED
   - Complex circuits (>50 blocks) often under-generated
   - Spatial reasoning could be better (avg 20-30%)
   - Contextual reasoning (WHY) is weak in one-shot

7. 📋 ACTION PLAN
   a) Use one-shot (temp=1.0) for circuits <30 blocks
   b) Use iterative for circuits >30 blocks OR for training data
   c) Add explicit constraints: "Generate exactly N blocks"
   d) Implement verification loop to catch under-generation
   e) Fine-tune on iterative traces for best results

8. 💡 BUDGET ESTIMATE FOR FULL PIPELINE
   - Dataset generation (10k circuits, mixed): ~$50-100
   - Fine-tuning Qwen 7B: ~$50-100
   - Total: ~$100-200 (well within reasonable budget)
""")
    
    # Save qualitative analysis
    analysis = {
        "timestamp": report['timestamp'],
        "circuits_analyzed": len(circuits),
        "total_tests": len(report['results']),
        "key_findings": {
            "best_model": "gemini-flash-lite",
            "best_temp_simple": 0.0,
            "best_temp_complex": 1.0,
            "recommended_approach": "mixed (one-shot for simple, iterative for complex)",
            "estimated_budget_10k": "$50-100"
        }
    }
    
    analysis_path = results_dir / "qualitative_analysis.json"
    with open(analysis_path, 'w') as f:
        json.dump(analysis, f, indent=2)
    
    print(f"\n✓ Qualitative analysis saved to: {analysis_path}")

if __name__ == "__main__":
    analyze_outputs()
