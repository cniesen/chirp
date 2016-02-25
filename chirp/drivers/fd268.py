# Copyright 2015 Pavel Milanes CO7WT, <co7wt@frcuba.co.cu> <pavelmc@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import struct
import os
import logging

from chirp import chirp_common, directory, memmap, errors, util, bitwise
from textwrap import dedent
from chirp.settings import RadioSettingGroup, RadioSetting, \
    RadioSettingValueBoolean, RadioSettingValueList, \
    RadioSettingValueString, RadioSettings

LOG = logging.getLogger(__name__)

MEM_FORMAT = """
#seekto 0x0010;
struct {
  lbcd rx_freq[4];
  lbcd tx_freq[4];
  lbcd rx_tone[2];
  lbcd tx_tone[2];
  u8 unknown:4,
     scrambler:1,
     unknown1:1,
     unknown2:1,
     busy_lock:1;
  u8 unknown3[3];
} memory[99];

#seekto 0x0640;
struct {
  lbcd vrx_freq[4];
  lbcd vtx_freq[4];
  lbcd vrx_tone[2];
  lbcd vtx_tone[2];
  u8 shift_plus:1,
     shift_minus:1,
     unknown11:2,
     scramble:1,
     unknown12:1,
     unknown13:1,
     busy_lock:1;
  u8 unknown14[3];
} vfo;

#seekto 0x07B0;
struct {
  u8 ani_mode;
  char ani[3];
  u8 unknown21[12];
  u8 unknown22:5,
     bw1:1,             // twin setting of bw (LCD "romb")
     bs1:1,             // twin setting of bs (LCD "S")
     warning1:1;        // twin setting of warning (LCD "Tune")
  u8 sql[1];
  u8 monitorval;
  u8 tot[1];
  u8 powerrank;
  u8 unknown23[3];
  u8 unknown24[8];
  char model[8];
  u8 unknown26[8];
  u8 step;
  u8 unknown27:2,
     power:1,
     lamp:1,
     lamp_auto:1,
     key:1,
     monitor:1,
     bw:1;
  u8 unknown28:3,
     warning:1,
     bs:1,
     unknown29:1,
     wmem:1,
     wvfo:1;
  u8 active_ch;
  u8 unknown30[4];
  u8 unknown31[4];
  bbcd vfo_shift[4];
} settings;
"""

MEM_SIZE = 0x0800
CMD_ACK = "\x06"
BLOCK_SIZE = 0x08
POWER_LEVELS = ["Low", "High"]
LIST_SQL = ["Off"] + ["%s" % x for x in range(1, 10)]
LIST_TOT = ["Off"] + ["%s" % x for x in range(10, 100, 10)]
ONOFF = ["Off", "On"]
STEPF = ["5", "10", "6.25", "12.5", "25"]
ACTIVE_CH = ["%s" % x for x in range(1, 100)]
KEY_LOCK = ["Automatic", "Manual"]
BW = ["Narrow", "Wide"]
W_MODE = ["VFO", "Memory"]
VSHIFT = ["None", "-", "+"]
POWER_RANK = ["%s" % x for x in range(0, 28)]
ANI = ["Off", "BOT", "EOT", "Both"]


def raw_recv(radio, amount):
    """Raw read from the radio device"""
    data = ""
    try:
        data = radio.pipe.read(amount)
    except:
        raise errors.RadioError("Error reading data from radio")

    return data


def raw_send(radio, data):
    """Raw write to the radio device"""
    try:
        data = radio.pipe.write(data)
    except:
        raise errors.RadioError("Error writing data to radio")


def make_frame(cmd, addr, length=BLOCK_SIZE):
    """Pack the info in the format it likes"""
    return struct.pack(">BHB", ord(cmd), addr, length)


def check_ack(r, text):
    """Check for a correct ACK from radio, raising error 'Text'
    if something was wrong"""
    ack = raw_recv(r, 1)
    if ack != CMD_ACK:
        raise errors.RadioError(text)
    else:
        return True


def send(radio, frame, data=""):
    """Generic send data to the radio"""
    raw_send(radio, frame)
    if data != "":
        raw_send(radio, data)
        check_ack(radio, "Radio didn't ack the last block of data")


def recv(radio):
    """Generic receive data from the radio, return just data"""
    # you must get it all 12 at once (4 header + 8 data)
    rxdata = raw_recv(radio, 12)
    if (len(rxdata) != 12):
        raise errors.RadioError(
            "Radio sent %i bytes, we expected 12" % (len(rxdata)))
    else:
        data = rxdata[4:]
        send(radio, CMD_ACK)
        check_ack(radio, "Radio didn't ack the sended data")
        return data


def do_magic(radio):
    """Try to get the radio in program mode, the factory software
    (FDX-288) tries up to ~16 times to get the correct response,
    we will do the same, but with a lower count."""
    tries = 8
    # UI information
    status = chirp_common.Status()
    status.cur = 0
    status.max = tries
    status.msg = "Linking to radio, please wait."
    radio.status_fn(status)

    # every byte of this magic chain must be send separatedly
    magic = "\x02PROGRA"

    # start the fun, finger crossed please...
    for a in range(0, tries):

        # UI update
        status.cur = a
        radio.status_fn(status)

        for i in range(0, len(magic)):
            send(radio, magic[i])

        # Now you get a x06 of ACK
        ack = raw_recv(radio, 1)
        if ack == CMD_ACK:
            return True

    return False


def do_program(radio):
    """Feidaxin program mode and identification dance"""
    # try to get the radio in program mode
    ack = do_magic(radio)
    if not ack:
        erc = "Radio did not accept program mode, "
        erc += "check your cable and radio; then try again."
        raise errors.RadioError(erc)

    # now we request identification
    send(radio, "M")
    send(radio, "\x02")
    ident = raw_recv(radio, 8)

    ################# WARNING ##########################################
    # Feidaxin radios has a "send id" procedure in the initial handshake
    # but it's worthless, once you do a hardware reset the ident area
    # get all 0xFF.
    #
    # Even FDX-288 software appears to match the model by any other
    # mean, so I detected on a few images that the 3 first bytes are
    # unique to each radio model, so for now we use that method untill
    # proven otherwise
    ####################################################################

    LOG.debug("Radio's ID string:")
    LOG.debug(util.hexprint(ident))

    # final ACK
    send(radio, CMD_ACK)
    check_ack(radio, "Radio refused to enter programming mode")


def do_download(radio):
    """ The download function """
    do_program(radio)
    # UI progress
    status = chirp_common.Status()
    status.cur = 0
    status.max = MEM_SIZE
    status.msg = "Cloning from radio..."
    radio.status_fn(status)
    data = ""
    for addr in range(0x0000, MEM_SIZE, BLOCK_SIZE):
        send(radio, make_frame("R", addr))
        d = recv(radio)
        data += d
        # UI Update
        status.cur = addr
        radio.status_fn(status)

    return memmap.MemoryMap(data)


def do_upload(radio):
    """The upload function"""
    do_program(radio)
    # UI progress
    status = chirp_common.Status()
    status.cur = 0
    status.max = MEM_SIZE
    status.msg = "Cloning to radio..."
    radio.status_fn(status)

    for addr in range(0x0000, MEM_SIZE, BLOCK_SIZE):
        send(radio, make_frame("W", addr),
             radio.get_mmap()[addr:addr+BLOCK_SIZE])
        # UI Update
        status.cur = addr
        radio.status_fn(status)


def model_match(cls, data):
    """Use a experimental guess to determine if the radio you just
    downloaded or the img opened you is for this model"""

    # Using a few imgs of some FD radio I found that the four first
    # bytes it's like the model fingerprint, so we have to testing the
    # model type with this experimental method so far.
    fp = data[0:4]
    if fp == cls._IDENT:
        return True
    else:
        LOG.debug("Unknowd Feidaxing radio, ID:")
        LOG.debug(util.hexprint(fp))

        return False


class FeidaxinFD2x8yRadio(chirp_common.CloneModeRadio):
    """Feidaxin FD-268 & alike Radios"""
    VENDOR = "Feidaxin"
    MODEL = "FD-268 & alike Radios"
    BAUD_RATE = 9600
    _memsize = MEM_SIZE
    _upper = 99
    _VFO_DEFAULT = 0
    _IDENT = ""
    _active_ch = ACTIVE_CH

    @classmethod
    def get_prompts(cls):
        rp = chirp_common.RadioPrompts()
        rp.experimental = \
            ('The program mode of this radio has his tricks, '
             'so this driver is *completely experimental*.')
        rp.pre_download = _(dedent("""\
            This radio has a tricky way of enter into program mode,
            even the original software has a few tries to get inside.

            I will try 8 times (most of the time ~3 will doit) and this
            can take a few seconds, if don't work, try again a few times.

            If you can get into it, please check the radio and cable.
            """))
        rp.pre_upload = _(dedent("""\
            This radio has a tricky way of enter into program mode,
            even the original software has a few tries to get inside.

            I will try 8 times (most of the time ~3 will doit) and this
            can take a few seconds, if don't work, try again a few times.

            If you can get into it, please check the radio and cable.
            """))
        return rp

    def get_features(self):
        """Return information about this radio's features"""
        rf = chirp_common.RadioFeatures()
        # this feature is READ ONLY by now.
        rf.has_settings = True
        rf.has_bank = False
        rf.has_tuning_step = False
        rf.has_name = False
        rf.has_offset = True
        rf.has_mode = False
        rf.has_dtcs = True
        rf.has_rx_dtcs = True
        rf.has_dtcs_polarity = True
        rf.has_ctone = True
        rf.has_cross = True
        rf.valid_duplexes = ["", "-", "+", "off"]
        rf.valid_tmodes = ['', 'Tone', 'TSQL', 'DTCS', 'Cross']
        # we have to remove "Tone->" because this is the same to "TQSL"
        # I get a few days hitting the wall with my head about this...
        rf.valid_cross_modes = [
            "Tone->Tone",
            "DTCS->",
            "->DTCS",
            "Tone->DTCS",
            "DTCS->Tone",
            "->Tone",
            "DTCS->DTCS"]
        # Power levels are golbal and no per channel, so disabled
        #rf.valid_power_levels = POWER_LEVELS
        # this radio has no skips
        rf.valid_skips = []
        # this radio modes are global and not per channel, so just FM
        rf.valid_modes = ["FM"]
        rf.valid_bands = [self._range]
        rf.memory_bounds = (1, self._upper)
        return rf

    def sync_in(self):
        """Do a download of the radio eeprom"""
        data = do_download(self)

        # as the radio comm protocol's identification is useless
        # we test the model after having the img
        if not model_match(self, data):
            # ok, wrong model, fire an error
            erc = "EEPROM fingerprint don't match, check if you "
            erc += "selected the right radio model."
            raise errors.RadioError(erc)

        # all ok
        self._mmap = data
        self.process_mmap()

    def sync_out(self):
        """Do an upload to the radio eeprom"""
        do_upload(self)

    def process_mmap(self):
        """Process the memory object"""
        self._memobj = bitwise.parse(MEM_FORMAT, self._mmap)

    def get_raw_memory(self, number):
        """Return a raw representation of the memory object"""
        return repr(self._memobj.memory[number])

    def _decode_tone(self, val):
        """Parse the tone data to decode from mem, it returns"""
        if val.get_raw() == "\xFF\xFF":
            return '', None, None

        val = int(val)
        if val >= 12000:
            a = val - 12000
            return 'DTCS', a, 'R'
        elif val >= 8000:
            a = val - 8000
            return 'DTCS', a, 'N'
        else:
            a = val / 10.0
            return 'Tone', a, None

    def _encode_tone(self, memval, mode, value, pol):
        """Parse the tone data to encode from UI to mem"""
        if mode == '':
            memval[0].set_raw(0xFF)
            memval[1].set_raw(0xFF)
        elif mode == 'Tone':
            memval.set_value(int(value * 10))
        elif mode == 'DTCS':
            flag = 0x80 if pol == 'N' else 0xC0
            memval.set_value(value)
            memval[1].set_bits(flag)
        else:
            raise Exception("Internal error: invalid mode `%s'" % mode)

    def get_memory(self, number):
        """Extract a high-level memory object from the low-level
        memory map, This is called to populate a memory in the UI"""
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[number - 1]
        # Create a high-level memory object to return to the UI
        mem = chirp_common.Memory()
        # number
        mem.number = number

        # empty
        if _mem.get_raw()[0] == "\xFF":
            mem.empty = True
            return mem

        # rx freq
        mem.freq = int(_mem.rx_freq) * 10

        # checking if tx freq is empty, this is "possible" on the
        # original soft after a warning, and radio is happy with it
        if _mem.tx_freq.get_raw() == "\xFF\xFF\xFF\xFF":
            mem.duplex = "off"
            mem.offset = 0
        else:
            rx = int(_mem.rx_freq) * 10
            tx = int(_mem.tx_freq) * 10
            if tx == rx:
                mem.offset = 0
                mem.duplex = ""
            else:
                mem.duplex = rx > tx and "-" or "+"
                mem.offset = abs(tx - rx)

        # tone data
        txtone = self._decode_tone(_mem.tx_tone)
        rxtone = self._decode_tone(_mem.rx_tone)
        chirp_common.split_tone_decode(mem, txtone, rxtone)

        # Extra setting group, FD-268 don't uset it at all
        # FD-288's & others do it?
        mem.extra = RadioSettingGroup("extra", "Extra")
        busy = RadioSetting("Busy", "Busy Channel Lockout",
                            RadioSettingValueBoolean(
                                bool(_mem.busy_lock)))
        mem.extra.append(busy)
        scramble = RadioSetting("Scrambler", "Scrambler Option",
                                RadioSettingValueBoolean(
                                    bool(_mem.scrambler)))
        mem.extra.append(scramble)

        # return mem
        return mem

    def set_memory(self, mem):
        """Store details about a high-level memory to the memory map
        This is called when a user edits a memory in the UI"""
        # Get a low-level memory object mapped to the image
        _mem = self._memobj.memory[mem.number - 1]

        # Empty memory
        if mem.empty:
            _mem.set_raw("\xFF" * 16)
            return

        # freq rx
        _mem.rx_freq = mem.freq / 10

        # freq tx
        if mem.duplex == "+":
            _mem.tx_freq = (mem.freq + mem.offset) / 10
        elif mem.duplex == "-":
            _mem.tx_freq = (mem.freq - mem.offset) / 10
        elif mem.duplex == "off":
            for i in range(0, 4):
                _mem.tx_freq[i].set_raw("\xFF")
        else:
            _mem.tx_freq = mem.freq / 10

        # tone data
        ((txmode, txtone, txpol), (rxmode, rxtone, rxpol)) = \
            chirp_common.split_tone_encode(mem)
        self._encode_tone(_mem.tx_tone, txmode, txtone, txpol)
        self._encode_tone(_mem.rx_tone, rxmode, rxtone, rxpol)

        # extra settings
        for setting in mem.extra:
            setattr(_mem, setting.get_name(), setting.value)

        return mem

    def get_settings(self):
        """Translate the bit in the mem_struct into settings in the UI"""
        _mem = self._memobj
        basic = RadioSettingGroup("basic", "Basic")
        work = RadioSettingGroup("work", "Work Mode Settings")
        top = RadioSettings(basic, work)

        # Basic
        sql = RadioSetting("settings.sql", "Squelch Level",
                           RadioSettingValueList(LIST_SQL, LIST_SQL[
                               _mem.settings.sql]))
        basic.append(sql)

        tot = RadioSetting("settings.tot", "Time out timer",
                           RadioSettingValueList(LIST_TOT, LIST_TOT[
                               _mem.settings.tot]))
        basic.append(tot)

        power = RadioSetting("settings.power", "Actual Power",
                             RadioSettingValueList(POWER_LEVELS,
                                 POWER_LEVELS[_mem.settings.power]))
        basic.append(power)

        key_lock = RadioSetting("settings.key", "Keyboard Lock",
                                RadioSettingValueList(KEY_LOCK,
                                    KEY_LOCK[_mem.settings.key]))
        basic.append(key_lock)

        bw = RadioSetting("settings.bw", "Bandwidth",
                          RadioSettingValueList(BW, BW[_mem.settings.bw]))
        basic.append(bw)

        powerrank = RadioSetting("settings.powerrank", "Power output adjust",
                                 RadioSettingValueList(POWER_RANK,
                                     POWER_RANK[_mem.settings.powerrank]))
        basic.append(powerrank)

        lamp = RadioSetting("settings.lamp", "LCD Lamp",
                            RadioSettingValueBoolean(_mem.settings.lamp))
        basic.append(lamp)

        lamp_auto = RadioSetting("settings.lamp_auto", "LCD Lamp auto on/off",
                                 RadioSettingValueBoolean(
                                     _mem.settings.lamp_auto))
        basic.append(lamp_auto)

        bs = RadioSetting("settings.bs", "Battery Save",
                          RadioSettingValueBoolean(_mem.settings.bs))
        basic.append(bs)

        warning = RadioSetting("settings.warning", "Warning Alerts",
                               RadioSettingValueBoolean(_mem.settings.warning))
        basic.append(warning)

        monitor = RadioSetting("settings.monitor", "Monitor key",
                               RadioSettingValueBoolean(_mem.settings.monitor))
        basic.append(monitor)

        # Work mode settings
        wmset = RadioSetting("settings.wmem", "VFO/MR Mode",
                             RadioSettingValueList(
                                 W_MODE, W_MODE[_mem.settings.wmem]))
        work.append(wmset)

        active_ch = RadioSetting("settings.active_ch", "Work Channel",
                                 RadioSettingValueList(ACTIVE_CH,
                                     ACTIVE_CH[_mem.settings.active_ch]))
        work.append(active_ch)

        # vfo rx validation
        if _mem.vfo.vrx_freq.get_raw()[0] == "\xFF":
            # if the vfo is not set, the UI cares about the
            # length of the field, so set a default
            LOG.debug("VFO freq not set, setting it to default %s" %
                self._VFO_DEFAULT)
            vfo = self._VFO_DEFAULT
        else:
            vfo = int(_mem.vfo.vrx_freq) * 10

        vf_freq = RadioSetting("vfo.vrx_freq", "VFO frequency",
                               RadioSettingValueString(0, 10,
                                   chirp_common.format_freq(vfo)))
        work.append(vf_freq)

        # shift works
        # VSHIFT = ["None", "-", "+"]
        sset = 0
        if bool(_mem.vfo.shift_minus) is True:
            sset = 1
        elif bool(_mem.vfo.shift_plus) is True:
            sset = 2

        shift = RadioSetting("shift", "VFO Shift",
                             RadioSettingValueList(VSHIFT, VSHIFT[sset]))
        work.append(shift)

        # vfo shift validation if none set it to ZERO
        if _mem.settings.vfo_shift.get_raw()[0] == "\xFF":
            # if the shift is not set, the UI cares about the
            # length of the field, so set to zero
            LOG.debug("VFO shift not set, setting it to zero")
            vfo_shift = 0
        else:
            vfo_shift = int(_mem.settings.vfo_shift) * 10

        offset = RadioSetting("settings.vfo_shift", "VFO Offset",
                              RadioSettingValueString(0, 9,
                                 chirp_common.format_freq(vfo_shift)))
        work.append(offset)

        step = RadioSetting("settings.step", "VFO step",
                            RadioSettingValueList(STEPF,
                                STEPF[_mem.settings.step]))
        work.append(step)

        # at least for FD-268A/B it doesn't work as stated, so disabled
        # by now
        #scamble = RadioSetting("vfo.scramble", "Scramble",
                                #RadioSettingValueList(ONOFF,
                                        #ONOFF[int(_mem.vfo.scramble)]))
        #work.append(scamble)

        #busy_lock = RadioSetting("vfo.busy_lock", "Busy Lock out",
                                #RadioSettingValueList(ONOFF,
                                        #ONOFF[int(_mem.vfo.busy_lock)]))
        #work.append(busy_lock)

        # FD-288 Family ANI settings
        if "FD-288" in self.MODEL:
            ani_mode = RadioSetting("settings.ani_mode", "ANI ID",
                                    RadioSettingValueList(ANI,
                                        ANI[_mem.settings.ani_mode]))
            work.append(ani_mode)

            # it can't be \xFF
            ani_value = str(_mem.settings.ani)
            if ani_value == "\xFF\xFF\xFF":
                ani_value = "200"

            ani_value = "".join(x for x in ani_value
                            if (int(x) >= 2 and int(x) <= 9))

            ani = RadioSetting("settings.ani", "ANI (200-999)",
                               RadioSettingValueString(0, 3, ani_value))
            work.append(ani)

        return top

    def set_settings(self, settings):
        """Translate the settings in the UI into bit in the mem_struct
        I don't understand well the method used in many drivers
        so, I used mine, ugly but works ok"""
        def _get_shift(obj):
            """Get the amount of offset in the memmap"""
            shift = 0
            sf = str(obj.settings.vfo_shift)
            if sf[0] != "\xFF":
                shift = int(mobj.settings.vfo_shift) * 10
            return shift

        def _get_vrx(obj):
            """Get the vfo rx freq"""
            return int(obj.vfo.vrx_freq) * 10

        def _update_vtx(o, rx, offset):
            """Update the Vfo TX mem from the value of rx & the offset"""
            # check the shift sign
            plus = bool(getattr(o, "shift_plus"))
            minus = bool(getattr(o, "shift_minus"))

            if plus:
                o.vtx_freq = (rx + offset) / 10
            elif minus:
                o.vtx_freq = (rx - offset) / 10
            else:
                o.vtx_freq = rx / 10

        mobj = self._memobj
        flag = False
        for element in settings:
            if not isinstance(element, RadioSetting):
                self.set_settings(element)
                continue

            # Let's roll the ball
            if "." in element.get_name():
                # real properties, more or less mapeable
                inter, setting = element.get_name().split(".")
                obj = getattr(mobj, inter)
                value = element.value

                # test on this cases .......
                if setting in ["sql", "tot", "powerrank", "active_ch",
                        "ani_mode"]:
                    value = int(value)

                # test on this cases .......
                if setting in ["lamp", "lamp_auto", "bs", "warning",
                        "monitor"]:
                    value = bool(value)
                    # warning and bs have a sister setting in LCD
                    if setting == "warning" or setting == "bs":
                        # aditional setting
                        setattr(obj, setting + "1", value)

                    # monitorval: monitor = 0 > monitorval = 0
                    # monitorval: monitor = 1 > monitorval = x30
                    if setting == "monitor":
                        # sister setting in LCD
                        if value:
                            setattr(obj, "monitorval", 0x30)
                        else:
                            setattr(obj, "monitorval", 0)

                # case power
                if setting == "power":
                    value = str(value) == "High" and True or False

                # case key
                # key => auto = 0, manu = 1
                if setting == "key":
                    value = str(value) == "Manual" and True or False

                # case bandwidth
                # bw: 0 = nar, 1 = Wide & must equal bw1
                if setting == "bw":
                    value = str(value) == "Wide" and True or False
                    # sister attr
                    setattr(obj, "bw1", value)

                # work mem wmem/wvfo
                if setting == "wmem":
                    if str(value) == "Memory":
                        value = True
                        # sister attr
                        setattr(obj, "wvfo", not value)
                    else:
                        value = False
                        # sister attr
                        setattr(obj, "wvfo", not value)

                # case step
                # STEPF = ["5", "10", "6.25", "12.5", "25"]
                if setting == "step":
                    value = STEPF.index(str(value))

                # case vrx_freq
                if setting == "vrx_freq":
                    value = chirp_common.parse_freq(str(value)) / 10
                    # you must calculate the apropiate txfreq from
                    # shift and offset
                    shift = _get_shift(mobj)
                    # update the tx vfo freq
                    _update_vtx(obj, value * 10, shift)

                # vfo_shift = offset
                if setting == "vfo_shift":
                    value = chirp_common.parse_freq(str(value)) / 10
                    # you must calculate the apropiate txfreq from
                    # shift and offset
                    # get vfo rx
                    vrx = _get_vrx(mobj)
                    # update the tx vfo freq
                    _update_vtx(mobj.vfo, vrx, value * 10)

                # at least for FD-268A/B it doesn't work as stated, so disabled
                # by now, does this work on the fd-288s?

                ## case scramble & busy_lock
                #if setting == "scramble" or setting == "busy_lock":
                    #value = bool(ONOFF.index(str(value)))

                # ani value, only for FD-288
                if setting == "ani":
                    if "FD-268" in self.MODEL:
                        # 268 doesn't have ani setting
                        continue
                    else:
                        # 288 models, validate the input [200-999] | ""
                        # we will left adjust and zero pad to avoid errors
                        # between inputs in the UI
                        value = str(value).strip().ljust(3, "0")
                        value = int(value)
                        if value == 0:
                            value = 200
                        else:
                            if value > 999 or value < 200:
                                raise errors.InvalidValueError(
                                    "The ANI value must be between \
                                    200 and 999, not %03i" % value)

                        value = str(value)

            else:
                # Others that are artifact for real values, not than mapeables
                # selecting the values to work
                setting = str(element.get_name())
                value = str(element.value)

                # vfo_shift
                if setting == "shift":
                    # get shift
                    offset = _get_shift(mobj)
                    # get vfo rx
                    vrx = _get_vrx(mobj)

                    # VSHIFT = ["None", "-", "+"]
                    if value == "+":
                        mobj.vfo.shift_plus = True
                        mobj.vfo.shift_minus = False
                    elif value == "-":
                        mobj.vfo.shift_plus = False
                        mobj.vfo.shift_minus = True
                    else:
                        mobj.vfo.shift_plus = False
                        mobj.vfo.shift_minus = False

                    # update the tx vfo freq
                    _update_vtx(mobj.vfo, vrx, offset)

            if setting != "shift":
                setattr(obj, setting, value)

    @classmethod
    def match_model(cls, filedata, filename):
        match_size = False
        match_model = False

        # testing the file data size
        if len(filedata) == MEM_SIZE:
            match_size = True

        # testing the firmware fingerprint, this experimental
        match_model = model_match(cls, filedata)

        if match_size and match_model:
            return True
        else:
            return False

## FD-268 family: this are the original tested models, FD-268B UHF
## was tested "remotely" with images thanks to AG5M
## I just have the 268A in hand to test


@directory.register
class FD268ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-268A Radio"""
    MODEL = "FD-268A"
    _range = (136000000, 174000000)
    _VFO_DEFAULT = 145000000
    _IDENT = "\xFF\xEE\x46\xFF"


@directory.register
class FD268BRadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-268B Radio"""
    MODEL = "FD-268B"
    _range = (400000000, 470000000)
    _VFO_DEFAULT = 439000000
    _IDENT = "\xFF\xEE\x47\xFF"

## FD-288 Family: the only difference from this family to the FD-268's
## are the ANI settings
## Tested hacking the FD-268A memmory


@directory.register
class FD288ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-288A Radio"""
    MODEL = "FD-288A"
    _range = (136000000, 174000000)
    _VFO_DEFAULT = 145000000
    _IDENT = "\xFF\xEE\x4B\xFF"


@directory.register
class FD288BRadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-288 Radio"""
    MODEL = "FD-288B"
    _range = (400000000, 470000000)
    _VFO_DEFAULT = 439000000
    _IDENT = "\xFF\xEE\x4C\xFF"

## the following radios was tested hacking the FD-268A memmory with
## the software and found to be clones of FD-268 ones


@directory.register
class FD150ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-150A Radio"""
    MODEL = "FD-150A"
    _range = (136000000, 174000000)
    _VFO_DEFAULT = 145000000
    _IDENT = "\xFF\xEE\x45\xFF"


@directory.register
class FD160ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-160A Radio"""
    MODEL = "FD-160A"
    _range = (136000000, 174000000)
    _VFO_DEFAULT = 145000000
    _IDENT = "\xFF\xEE\x48\xFF"


@directory.register
class FD450ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-450A Radio"""
    MODEL = "FD-450A"
    _range = (400000000, 470000000)
    _VFO_DEFAULT = 439000000
    _IDENT = "\xFF\xEE\x44\xFF"


@directory.register
class FD460ARadio(FeidaxinFD2x8yRadio):
    """Feidaxin FD-460A Radio"""
    MODEL = "FD-460A"
    _range = (400000000, 470000000)
    _VFO_DEFAULT = 439000000
    _IDENT = "\xFF\xEE\x4A\xFF"

