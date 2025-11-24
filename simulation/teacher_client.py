import textwrap

class TeacherClient:
    """
    Handles interactions with the Teacher Model (LLM) to generate:
    1. Functional Tests (The Verification Contract)
    2. Reasoning Traces (The Chain-of-Thought)
    """

    def __init__(self, model_name="gpt-4o"):
        self.model_name = model_name

    def generate_test_contract(self, working_code: str, meta: dict) -> str:
        """
        Prompt the LLM to generate a Python test script for the given circuit.
        """
        name = meta.get("name", "Unknown Schematic")
        desc = meta.get("description", "No description provided.")
        
        # Mock Response Logic
        name_lower = name.lower()
        if "lamp" in name_lower:
            return self._mock_lamp_test(name, desc)
        elif "hopper" in name_lower:
            return self._mock_hopper_test(name, desc)
        elif "door" in name_lower or "gate" in name_lower:
            return self._mock_door_test(name, desc)
        
        return self._mock_generic_test(name)

    def generate_reasoning_trace(self, context: str, broken_code: str, error_log: str, fixed_code: str) -> dict:
        """
        Prompt the LLM to hallucinate the reasoning process for fixing the circuit.
        """
        trace = textwrap.dedent(f"""
            <THOUGHT>
            The test failed with: "{error_log}".
            Analyzing the broken code, I see that the circuit state does not match the requirements.
            The error indicates a failure in signal transmission or logic processing.
            I suspect a component (wire, repeater, or device) is missing or misconfigured.
            I will restore the circuit to the known working configuration found in the reference design.
            </THOUGHT>
        """).strip()
        
        return {"teacher_trace": trace}

    def _mock_lamp_test(self, name, desc):
        return textwrap.dedent(f"""
        import time

        def run_test(bridge, origin):
            '''
            Test for {name}
            Circuit: Lever (0,1,0) -> Wire (1,1,0) -> Lamp (2,1,0)
            '''
            ox, oy, oz = origin
            
            def check_state(rx, ry, rz, expect_state):
                ax, ay, az = ox + rx, oy + ry, oz + rz
                resp = bridge.run_command(f"mira_api check_block {{ax}} {{ay}} {{az}} {{expect_state}}")
                if "FAIL" in resp:
                    raise Exception(f"Assertion Failed at {{rx}},{{ry}},{{rz}}: Expected {{expect_state}}, got {{resp}}")

            print("Test: Verifying Initial State (Lamp OFF)...")
            # Ensure Lever OFF
            bridge.set_block(ox, oy+1, oz, "minecraft:lever[face=floor,facing=east,powered=false]")
            bridge.run_command(f"mira_api update_block {{ox}} {{oy+1}} {{oz}}")
            
            bridge.run_command("tick step 10")
            time.sleep(0.5)
            
            check_state(2, 1, 0, "minecraft:redstone_lamp[lit=false]")
            
            print("Test: Activating Lever (Lamp ON)...")
            bridge.set_block(ox, oy+1, oz, "minecraft:lever[face=floor,facing=east,powered=true]")
            # Trigger updates
            bridge.run_command(f"mira_api update_block {{ox}} {{oy+1}} {{oz}}")
            
            # Wait for signal propagation
            bridge.run_command("tick step 10")
            time.sleep(0.5)
            
            check_state(2, 1, 0, "minecraft:redstone_lamp[lit=true]")
            
            print("Test: Success.")
            return "PASS"
        """)

    def _mock_hopper_test(self, name, desc):
        return textwrap.dedent(f"""
        import time

        def run_test(bridge, origin):
            '''
            Test for {name}
            Circuit: Chest (0,2,0) -> Hopper (0,1,0) -> Chest (0,0,0)
            '''
            ox, oy, oz = origin
            
            print("Test: Setup - Filling Top Chest...")
            
            # Use variables for NBT to avoid f-string escaping hell
            # We want the generated code to have literal strings with braces
            nbt_fill = "{{Items:[{{id:'minecraft:stone',Count:64b,Slot:0b}}]}}"
            nbt_empty = "{{Items:[]}}"
            
            # Set Top Chest with Items
            bridge.run_command(f"data merge block {{ox}} {{oy+2}} {{oz}} {{nbt_fill}}")
            # Clear Bottom Chest
            bridge.run_command(f"data merge block {{ox}} {{oy}} {{oz}} {{nbt_empty}}")
            
            print("Test: Waiting for item transfer...")
            # Hopper transfer rate: 2.5 items/sec (8 game ticks per item)
            # We step 20 ticks (1 sec) -> should transfer ~2-3 items
            bridge.run_command("tick step 20")
            time.sleep(1.0)
            
            print("Test: Checking Bottom Chest...")
            resp = bridge.run_command(f"data get block {{ox}} {{oy}} {{oz}} Items")
            
            if "minecraft:stone" not in resp:
                raise Exception(f"Hopper failed to transfer items. Bottom chest data: {{resp}}")
                
            print("Test: Success.")
            return "PASS"
        """)

    def _mock_door_test(self, name, desc):
        return textwrap.dedent(f"""
        import time

        def run_test(bridge, origin):
            '''
            Test for {name}
            '''
            ox, oy, oz = origin
            
            print("Test: Door Logic Stub")
            return "PASS"
        """)

    def _mock_generic_test(self, name):
        return textwrap.dedent(f"""
        def run_test(bridge, origin):
            print("Test: Default generic check for {name}.")
            return "PASS"
        """)
