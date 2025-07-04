#!/usr/bin/env python
import argparse
import csv
import logging
import os
import shutil
import subprocess
import time
from configparser import ConfigParser
from pathlib import Path
from typing import Any, List

from cocotb_simulate_test import get_block_ini
from cocotb_tools import runner  # type: ignore

logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
TOP = SCRIPT_DIR.parent
WORKING_DIR = Path.cwd()


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("test_name", nargs="?", default=None)
    parser.add_argument("--sim", default="nvc")
    parser.add_argument("--skip", default=None)
    parser.add_argument("--panda-src", default="/src")
    parser.add_argument("--panda-build-dir", default="/build")
    parser.add_argument("-c", action="store_true")
    return parser.parse_args()


def read_ini(path: List[str] | str) -> ConfigParser:
    """Read INI file and return its contents.

    Args:
        path: Path to INI file.
    Returns:
        ConfigParser object containing INI file.
    """
    app_ini = ConfigParser()
    app_ini.read(path)
    return app_ini


def get_timing_inis(panda_src: str, module: str) -> dict[str, ConfigParser]:
    """Get a module's timing ini files.

    Args:
        module: Name of module.
    Returns:
        Dictionary of filepath: file contents for any timing.ini files in the
        module directory.
    """
    ini_paths = (Path(panda_src) / "modules" / module).glob("*.timing.ini")
    return {str(path): read_ini(str(path.resolve())) for path in ini_paths}


def block_has_dma(block_ini: ConfigParser) -> bool:
    """Check if module requires a dma to work.

    Args:
        block_ini: INI file containing signals information about a module.
    """
    return block_ini["."].get("type", "") == "dma"


def get_module_build_args(module: str, panda_src: str | Path,
                          panda_build_dir: str | Path) -> list[str]:
    """Get simulation build arguments from a module's test config file.

    Args:
        module: Name of module.
        panda_build_dir: Path to autogenerated HDL files.
    Returns:
        List of extra build arguments.
    """
    test_config_path = SCRIPT_DIR / f'{module}_test_config.py'
    if test_config_path.exists():
        g = {"TOP": Path(panda_src), "EXTRA": TOP / "hdl"}
        code = open(str(test_config_path)).read()
        exec(code, g)
        args: list[str] = g.get("EXTRA_BUILD_ARGS", [])  # type: ignore
        return args
    return []


def order_hdl_files(
    hdl_files: list[Path], build_dir: str | Path, top_level: str
) -> list[Path]:
    """Put vhdl source files in compilation order. This is neccessary for the
    nvc simulator as it does not order the files iself before compilation.

    Args:
        hdl_files: List of vhdl source files.
        build_dir: Build directory for simulation.
        top_level: Name of the top-level entity.
    """
    command: list[str] = [
        "vhdeps",
        "dump",
        top_level,
        "-o",
        f'{WORKING_DIR / build_dir / "order"}',
    ]
    for file in hdl_files:
        command.append(f"--include={str(file)}")
    command_str = " ".join(command)
    Path(WORKING_DIR / build_dir).mkdir(exist_ok=True)
    subprocess.run(["/usr/bin/env"] + command)
    try:
        with open(Path(build_dir) / "order") as order:
            ordered_hdl_files = [
                Path(line.strip().split(" ")[-1]) for line in order.readlines()
            ]
        return ordered_hdl_files
    except FileNotFoundError as error:
        logger.warning(f"Likely that the following command failed:\n{command_str}")
        logger.warning(error)
        logger.warning("HDL FILES HAVE NOT BEEN PUT INTO COMPILATION ORDER!")
        return hdl_files


def get_module_hdl_files(
        module: str, module_top_level: Path, panda_src: str | Path,
        build_dir: str | Path, panda_build_dir: str | Path
):
    """Get HDL files needed to simulate a module from its test config file.

    Args:
        module: Name of module.
        top_level: Top level entity of module being tested.
        build_dir: Name of simulation build directory.
        panda_build_dir: Path to autogenerated HDL files.
    Returns:
        List of paths to the HDL files.
    """
    test_config_path = SCRIPT_DIR / f'{module}_test_config.py'
    g = {"TOP": Path(panda_src), "EXTRA": TOP / "hdl"}
    if test_config_path.exists():
        code = open(str(test_config_path)).read()
        exec(code, g)
        g.get("EXTRA_HDL_FILES", [])
        extra_files: list[Path] = list(g.get("EXTRA_HDL_FILES", []))  # type: ignore
        extra_files_2: list[Path] = []
        for my_file in extra_files:
            if str(my_file).endswith(".vhd"):
                extra_files_2.append(my_file)
            else:
                extra_files_2 = extra_files_2 + list(my_file.glob("**/*.vhd"))
    else:
        extra_files_2 = []
    result = extra_files_2 + list((Path(panda_src) / "modules" / module / "hdl").glob("*.vhd"))
    ordered = order_hdl_files(result, build_dir, module_top_level)
    logger.info("Gathering the following VHDL files:")
    for my_file in ordered:
        logger.info(my_file)
    return ordered


def print_results(
    module: str, passed: list[str], failed: list[str], time: float | None = None
):
    """Format and print results from a module's tests.

    Args:
        module: Name of module.
        passed: List of the names of tests that passed.
        failed: List of the names of tests that failed.
        time: Time taken to run the tests.
    """
    print("__")
    print("\nModule: {}".format(module))
    if len(passed) + len(failed) == 0:
        print("\033[0;33m" + "No tests ran." + "\033[0m")
    else:
        percentage = round(len(passed) / (len(passed) + len(failed)) * 100)
        print(
            "{}/{} tests passed ({}%).".format(
                len(passed), len(passed) + len(failed), percentage
            )
        )
        if time is not None:
            print("Time taken = {}s.".format(time))
        if failed:
            print("\033[0;31m" + "Failed tests:" + "\x1b[0m", end=" ")
            print(
                *[
                    test + (", " if i < len(failed) - 1 else ".")
                    for i, test in enumerate(failed)
                ]
            )
        else:
            print("\033[92m" + "ALL PASSED" + "\x1b[0m")


def summarise_results(results: dict[str, list[list[str]]]):
    """Format and print summary of results from a test run.

    Args:
        Results: Dictionary of all results from a test run.
    """
    failed: list[str] = [module for module in results if results[module][1]]
    passed: list[str] = [module for module in results if not results[module][1]]
    total_passed, total_failed = 0, 0
    for module in results:
        total_passed += len(results[module][0])
        total_failed += len(results[module][1])
    total = total_passed + total_failed
    print("\nSummary:\n")
    if total == 0:
        print("\033[1;33m" + "No tests ran." + "\033[0m")
    else:
        print(
            "{}/{} modules passed ({}%).".format(
                len(passed),
                len(results.keys()),
                round(len(passed) / len(results.keys()) * 100),
            )
        )
        print(
            "{}/{} tests passed ({}%).".format(
                total_passed, total, round(total_passed / total * 100)
            )
        )
        if failed:
            print("\033[0;31m" + "\033[1m" + "Failed modules:" + "\x1b[0m", end=" ")
            print(
                *[
                    module + (", " if i < len(failed) - 1 else ".")
                    for i, module in enumerate(failed)
                ]
            )
        else:
            print("\033[92m" + "\033[1m" + "ALL MODULES PASSED" + "\x1b[0m")


def get_simulator_build_args(simulator: str) -> list[str]:
    """Get arguments for the build stage.

    Args:
        simulator: Name of simulator being used.
    Returns:
        List of build arguments.
    """
    if simulator == "ghdl":
        return ["--std=08", "-fsynopsys", "-Wno-hide"]
    elif simulator == "nvc":
        return ["--std=2008"]
    else:
        raise NotImplementedError(f"{simulator} is not a valid simulator")


def get_test_args(simulator: str, build_args: list[str], test_name: str) -> list[str]:
    """Get arguments for the test stage.

    Args:
        simulator: Name of simulator being used.
        build_args: Arguments used for the build stage.
        test_name: Name of test being carried out.
    Returns:
        List of test arguments.
    """
    test_name = test_name.replace(" ", "_").replace("/", "_")
    if simulator == "ghdl":
        return build_args
    elif simulator == "nvc":
        return ["--ieee-warnings=off", f"--wave={test_name}.vcd"]
    else:
        raise NotImplementedError(f"{simulator} is not a valid simulator")


def get_elab_args(simulator: str) -> list[str]:
    """Get arguments for the elaboration stage.

    Args:
        simulator: Name of simulator being used.
    Returns:
        List of elaboration arguments.
    """
    if simulator == "nvc":
        return ["--cover"]
    else:
        return []


def get_plusargs(simulator: str, test_name: str) -> list[str]:
    """Get plusargs to for the test stage.

    Args:
        simulator: Name of simulator being used.
        test_name: Name of test being carried out.

    Returns:
    """
    test_name = test_name.replace(" ", "_").replace("/", "_")
    vcd_filename = f"{test_name}.vcd"
    if simulator == "ghdl":
        return [f"--vcd={vcd_filename}"]
    elif simulator == "vcd":
        return []
    return []


def collect_coverage_file(
    build_dir: str | Path, top_level: str, test_name: str
) -> Path:
    """Move coverage file to the coverage directory

    Args:
        build_dir: Simulation build directory.
        top_level: Top level entity being tested.
        test_name: Name of test being carried out.
    Returns:
        New file path of the coverage file.
    """
    coverage_path = Path(WORKING_DIR / build_dir / "coverage")
    Path(coverage_path).mkdir(exist_ok=True)
    old_file_path = Path(
        WORKING_DIR / build_dir / "top" / f"_TOP.{top_level.upper()}.elab.covdb"
    )
    test_name = test_name.replace(" ", "_").replace("/", "_")
    new_file_path = Path(
        coverage_path / f"_TOP.{top_level.upper()}.{test_name}.elab.covdb"
    )
    subprocess.run(["mv", old_file_path, new_file_path])
    return new_file_path


def merge_coverage_data(
    build_dir: str | Path, module: str, file_paths: list[Path]
) -> Path:
    """Merges coverage files from each test to create an overall coverage
    report for a module.

    Args:
        build_dir: Simulation build directory.
        module: Name of module.
        file_paths: List of paths to coverage files from each test.
    Returns:
        File path for the coverage report file.
    """
    merged_path = Path(WORKING_DIR / build_dir / "coverage" / f"merged.{module}.covdb")
    command = (
        ["nvc", "--cover-merge", "-o"]
        + [str(merged_path)]
        + [str(file_path) for file_path in file_paths]
    )
    subprocess.run(command)
    return merged_path


def export_coverage_data(
    output_path: Path,
    file_paths: list[Path],
    format: str = "cobertura",
):
    """Merges merge coverage files from each module to create an overall coverage
    report in an xml format suitable for codecov.

    Args:
        output_path: Path of the output coverage report to write
        file_paths: List of Paths to coverage files from each module.
        format: coverage format to pass to nvc simulator
    """
    command = (
        ["nvc", "--cover-export", f"--format={format}", "-o"]
        + [str(output_path)]
        + [str(file_path) for file_path in file_paths]
    )
    subprocess.run(command)


def cleanup_dir(test_name: str, build_dir: str | Path):
    """Creates a subdirectory for a test and moves all files generated from
    that test into it.

    Args:
        test_name: Name of test.
        build_dir: Simulation build directory.
    """
    test_name = test_name.replace(" ", "_").replace("/", "_")
    (WORKING_DIR / build_dir / test_name).mkdir(exist_ok=True)
    logger.info(f'Putting all files related to "{test_name}" in {str(
        WORKING_DIR / build_dir / test_name)}')
    for file in (WORKING_DIR / build_dir).glob(f"{test_name}*"):
        if file.is_file():
            new_name = str(file).split("/")[-1].replace(test_name, "")
            if new_name.endswith(".vcd"):
                new_name = "wave" + new_name
            new_name = new_name.lstrip("_")
            file.rename(WORKING_DIR / build_dir / test_name / new_name)


def print_errors(failed_tests: list[str], build_dir: str | Path):
    """Print out timing errors.

    Args:
        failed_tests: List of tests that failed.
        build_dir: Simulation build directory.
    """
    for test_name in failed_tests:
        logger.info(f'        See timing errors for "{test_name}" below')
        test_name = test_name.replace(" ", "_").replace("/", "_")
        with open(WORKING_DIR / build_dir / test_name / "errors.csv") as file:
            reader = csv.reader(file)
            for row in reader:
                log_timing_error(row[1])


def print_coverage_data(coverage_report_path: Path):
    """Print coverage report

    Args:
        coverage_report_path: Path to coverage report file.
    """
    print("Code coverage:")
    coverage_path = coverage_report_path.parent
    command = [
        "nvc",
        "--cover-report",
        "-o",
        str(coverage_path),
        str(coverage_report_path),
    ]
    subprocess.run(command)


def log_timing_error(message: str, *args: Any, **kwargs: Any):
    logger.error(message, *args, **kwargs)
    #timing_error_level = 30
    #if logger.isEnabledFor(timing_error_level):
    #    logger.error(message, *args, **kwargs)


def test_module(
    module: str,
    test_name: str | None = None,
    simulator: str = "nvc",
    panda_src: str | Path = "/src",
    panda_build_dir: str | Path = "/build",
    collect: bool = False,
) -> tuple[list[str], list[str], Path | None]:
    """Run tests for a module.

    Args:
        module: Name of module.
        test_name: Name of specific test to run. If not specified, all tests
            for that module will be run.
        simulator: Name of simulator to use for simulation.
        panda_build_dir: Location of autogenerated HDL files.
        collect: If True, collect output signals expected and actual values.
    Returns:
        Lists of tests that passed and failed respectively, path to coverage.
    """
    sim: runner.Simulator = runner.get_runner(simulator)  # type: ignore
    build_dir = f"sim_build_{module}"
    build_args = get_simulator_build_args(simulator)
    build_args += get_module_build_args(module, panda_src, panda_build_dir)
    top_level = module
    sim.build(  # type: ignore
        sources=get_module_hdl_files(module, top_level, panda_src, build_dir,
                                     panda_build_dir),
        build_dir=build_dir,
        hdl_toplevel=top_level,
        build_args=build_args,
        clean=True,
    )
    passed: list[str] = []
    failed: list[str] = []
    coverage_file_paths: list[Path] = []
    coverage_report_path: Path | None = None

    timing_inis = get_timing_inis(panda_src, module)
    for path, timing_ini in timing_inis.items():
        if test_name:
            sections: list[str] = []
            for test in test_name.split(",,"):  # Some test names contain a comma
                if test in timing_ini.sections():
                    sections.append(test)
                else:
                    print(
                        'No test called "{}" in {} INI timing file.'.format(
                            test, module
                        ).center(shutil.get_terminal_size().columns)
                    )
            if not sections:
                return [], [], None
        else:
            sections = timing_ini.sections()

        for section in sections:
            if section.strip() != ".":
                test_name = section
                print()
                print('Test: "{}" in module {}.\n'.format(test_name, module))
                xml_path: Path = sim.test(  # type: ignore
                    hdl_toplevel=top_level,
                    test_module="cocotb_simulate_test",
                    build_dir=build_dir,
                    test_args=get_test_args(simulator, build_args, test_name),
                    elab_args=get_elab_args(simulator),
                    plusargs=get_plusargs(simulator, test_name),
                    extra_env={
                        "module": module,
                        "test_name": test_name,
                        "simulator": simulator,
                        "sim_build_dir": str(build_dir),
                        "timing_ini_path": str(path),
                        "panda_src_dir": str(panda_src),
                        "panda_build_dir": str(panda_build_dir),
                        "collect": str(collect),
                    },
                )
                results: tuple[int, int] = runner.get_results(xml_path)  # type: ignore
                if simulator == "nvc":
                    coverage_file_paths.append(
                        collect_coverage_file(build_dir, top_level, test_name)
                    )
                if results == (1, 0):
                    # ran 1 test, 0 failed
                    passed.append(test_name)
                elif results == (1, 1):
                    # ran 1 test, 1 failed
                    failed.append(test_name)
                else:
                    raise ValueError(f"Results unclear: {results}")
                cleanup_dir(test_name, build_dir)
        test_name = None
    if simulator == "nvc":
        coverage_report_path = merge_coverage_data(
            build_dir, module, coverage_file_paths
        )
    return passed, failed, coverage_report_path


def run_tests():
    """Perform test run."""
    t_time_0 = time.time()
    args = get_args()
    if args.module.lower() == "all":
        modules = ['pgen', 'seq']
    else:
        modules = args.module.split(",")
    skip_list: list[str] = args.skip.split(",") if args.skip else []
    for module in skip_list:
        if module in modules:
            modules.remove(module)
            print(f"Skipping {module}.")
        else:
            print(f"Cannot skip {module} as it was not going to be tested.")
    simulator = args.sim
    collect = bool(args.c)
    results: dict[str, list[list[str]]] = {}
    times: dict[str, float | None] = {}
    coverage_reports: dict[str, Path | None] = {}
    for module in modules:
        t0 = time.time()
        module = module.strip("\n")
        results[module] = [[], []]
        # [[passed], [failed]]
        print()
        print(
            "* Testing module \033[1m{}\033[0m *".format(module.strip("\n")).center(
                shutil.get_terminal_size().columns
            )
        )
        print(
            "---------------------------------------------------".center(
                shutil.get_terminal_size().columns
            )
        )
        results[module][0], results[module][1], coverage_reports[module] = test_module(
            module,
            test_name=args.test_name,
            simulator=simulator,
            panda_src=args.panda_src,
            panda_build_dir=args.panda_build_dir,
            collect=collect,
        )
        t1 = time.time()
        times[module] = round(t1 - t0, 2)
    print("___________________________________________________")
    print("\nResults:")
    for module in results:
        print_results(module, results[module][0], results[module][1], times[module])
        path = coverage_reports[module]
        if path is not None:
            print_coverage_data(path)
        build_dir = f"sim_build_{module}"
        print_errors(results[module][1], build_dir)
    print("___________________________________________________")
    summarise_results(results)
    t_time_1 = time.time()
    print("\nTime taken: {}s.".format(round(t_time_1 - t_time_0, 2)))
    print("___________________________________________________\n")
    print(f"Simulator: {simulator}\n")

    report_paths = [path for path in coverage_reports.values() if path is not None]
    export_coverage_data(Path("cocotb_coverage.xml"), report_paths)


def main():
    run_tests()


if __name__ == "__main__":
    main()
