# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import microcotb as cocotb
from microcotb.clock import Clock
from microcotb.triggers import ClockCycles



SCK_BIT = 3
MOSI_BIT = 1
CS_BIT = 2

MISO_BIT = 0
READY_BIT = 1
TESTING_BIT = 2
INREADY_BIT = 3
VALID_BIT = 4

SPI_EDGE_WAIT_CYCLES = 50


def pack_ui(sck=0, mosi=0, cs=1):
    return (sck << SCK_BIT) | (mosi << MOSI_BIT) | (cs << CS_BIT)


def get_uo_bit(dut, bit_index):
    return int(dut.uo_out[bit_index])

# Code version of matrix multiplication to use as golden model.
def matrix_multiply(a_matrix, b_matrix):
    return [
        [
            sum(a_matrix[row][k] * b_matrix[k][col] for k in range(3))
            for col in range(3)
        ]
        for row in range(3)
    ]

# Turns the matricies into data that is ready to be fed to SPI
def flatten_input_words(a_matrix, b_matrix):
    words = []

    for row in a_matrix:
        words.extend(row)

    for col in range(3):
        for row in range(3):
            words.append(b_matrix[row][col])

    return words

# polls output bit until it becomes expected value or will raise an error.
async def wait_for_output_bit(dut, bit_index, value=1, timeout_cycles=500):
    for _ in range(timeout_cycles):
        if get_uo_bit(dut, bit_index) == value:
            return
        await ClockCycles(dut.clk, 1)
    raise AssertionError(f"Timed out waiting for uo_out[{bit_index}] to become {value}")

# writes SPI signal state to ui_in.
async def drive_spi_state(dut, spi_state):
    dut.ui_in.value = pack_ui(**spi_state)

# Drives SCK high then low based on wait cycles specified above.
async def spi_pulse_sck(dut, spi_state):
    spi_state["sck"] = 1
    await drive_spi_state(dut, spi_state)
    await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)

    spi_state["sck"] = 0
    await drive_spi_state(dut, spi_state)
    await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)

# Sends a 4bit nibble MSB first.
async def spi_send_nibble(dut, spi_state, nibble):
    for shift in range(3, -1, -1):
        spi_state["mosi"] = (nibble >> shift) & 1
        spi_state["sck"] = 0
        await drive_spi_state(dut, spi_state)
        await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)
        await spi_pulse_sck(dut, spi_state)

# Sends the full frame.
async def spi_send_frame(dut, words):
    spi_state = {"sck": 0, "mosi": 0, "cs": 1}
    await drive_spi_state(dut, spi_state)
    await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)

    spi_state["cs"] = 0
    await drive_spi_state(dut, spi_state)
    await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)

    for word in words:
        await spi_send_nibble(dut, spi_state, word)

    spi_state["mosi"] = 0
    spi_state["cs"] = 1
    spi_state["sck"] = 0
    await drive_spi_state(dut, spi_state)
    await ClockCycles(dut.clk, SPI_EDGE_WAIT_CYCLES)

    return spi_state

#reads results back from MISO.
async def spi_read_results(dut, spi_state):
    await wait_for_output_bit(dut, TESTING_BIT, value=1, timeout_cycles=500)

    # The first readout edge advances the top-level FSM from ST_WAIT to ST_READ.
    # The second edge loads the first MISO bit.
    await spi_pulse_sck(dut, spi_state)
    await spi_pulse_sck(dut, spi_state)

    bits = [get_uo_bit(dut, MISO_BIT)]
    for _ in range(89):
        await spi_pulse_sck(dut, spi_state)
        bits.append(get_uo_bit(dut, MISO_BIT))

    words = []
    for word_index in range(9):
        value = 0
        for bit in bits[word_index * 10 : (word_index + 1) * 10]:
            value = (value << 1) | bit
        words.append(value)

    await spi_pulse_sck(dut, spi_state)

    return words


@cocotb.test()
async def test_project_matrix_multiply(dut):
    clock = Clock(dut.clk, 100, units="us")
    cocotb.start_soon(clock.start())

    a_matrix = [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9],
    ]
    b_matrix = [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9],
    ]
    expected = matrix_multiply(a_matrix, b_matrix)
    expected_words = [value for row in expected for value in row]

    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.ui_in.value = pack_ui(cs=1)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)

    await wait_for_output_bit(dut, INREADY_BIT, value=1, timeout_cycles=100)

    frame_words = flatten_input_words(a_matrix, b_matrix)
    spi_state = await spi_send_frame(dut, frame_words)

    await wait_for_output_bit(dut, VALID_BIT, value=1, timeout_cycles=50)
    await wait_for_output_bit(dut, INREADY_BIT, value=1, timeout_cycles=50)

    observed_words = await spi_read_results(dut, spi_state)

    assert observed_words == expected_words, (
        f"Matrix multiply mismatch. Expected {expected_words}, got {observed_words}"
    )

    await ClockCycles(dut.clk, 5)
    assert get_uo_bit(dut, READY_BIT) == 0, "READY flag should clear after result readout"

# Added these packages for testing on the board
from ttboard.demoboard import DemoBoard
from ttboard.cocotb.dut import DUT

# Added in hopes this fixes pin detection
from ttboard.boot.demoboard_detect import DemoboardDetect, DemoboardVersion

# This main function is what is exposed to the init so that the test can be run.
def main():
    # Also addded to help with pin detection
    DemoboardDetect.force_detection(DemoboardVersion.TTDBv3)

    tt = DemoBoard.get()
    
    # make certain this chip has the project
    if not tt.shuttle.has('tt_um_factory_test'):
        print("Project is missing or name was changed.")
        return
    
    # enable the project
    tt.shuttle.tt_um_factory_test.enable()
    
    dut = DUT()
    dut._log.info("enabled project, running, running tests")
    runner = cocotb.get_runner()
    runner.test(dut)
    
