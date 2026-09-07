from pathlib import Path
import runpy


def test_abby_example_reconstructs_live_mainnet_transaction():
    example = runpy.run_path(str(Path(__file__).parents[1] / "examples" / "abby_launch.py"))
    calldata = example["ABBY_CALLDATA"]
    deployment = example["ABBY_DEPLOYMENT"]

    assert calldata.hex().startswith("e5ac002e")
    assert len(calldata) == 1_060
    assert deployment["transaction_hash"] == "0xd0dcae27e9ec2f7fb6e2304d7b1d739fd2f45f91d1eb5e762cdfcf01819fb837"
    assert deployment["token"] == "0x450b50d216088e40cdd412da98b3b4c07bb4931f"
    assert deployment["pool"] == "0x5304f1300384d129d7f18a2b657d9cc6ff2cc002"
