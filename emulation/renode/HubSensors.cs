// Register-level models for the MPU6050/BMP280 operations used by this project.
// These model digital register behavior, not sensor physics or electrical timing.
using System;
using Antmicro.Renode.Peripherals.I2C;

namespace Antmicro.Renode.Peripherals.Sensors
{
    public class HubMPU6050 : II2CPeripheral
    {
        public HubMPU6050() { Reset(); }
        public void Reset()
        {
            Array.Clear(registers, 0, registers.Length);
            registers[0x75] = 0x68;
            registers[0x6B] = 0x40;
            pointer = 0;
            AccelerationX = 0; AccelerationY = 0; AccelerationZ = 16384;
            GyroscopeX = 0; GyroscopeY = 0; GyroscopeZ = 0;
            Temperature = 0;
            SampleReads = 0;
        }
        public void Write(byte[] data)
        {
            if(data.Length == 0) return;
            pointer = data[0];
            for(var i = 1; i < data.Length; ++i)
            {
                if(pointer == 0x6B && (data[i] & 0x80) != 0) { Reset(); return; }
                if(pointer != 0x75) registers[pointer] = data[i];
                pointer++;
            }
        }
        public byte[] Read(int count = 1)
        {
            var result = new byte[count];
            for(var i = 0; i < count; ++i)
            {
                if(pointer == 0x3B && (registers[0x6B] & 0x40) == 0)
                {
                    Put16(0x3B, AccelerationX); Put16(0x3D, AccelerationY); Put16(0x3F, AccelerationZ);
                    Put16(0x41, Temperature);
                    Put16(0x43, GyroscopeX); Put16(0x45, GyroscopeY); Put16(0x47, GyroscopeZ);
                    SampleReads++;
                }
                result[i] = registers[pointer++];
            }
            return result;
        }
        public void FinishTransmission() { }
        public int AccelerationX { get; set; }
        public int AccelerationY { get; set; }
        public int AccelerationZ { get; set; }
        public int GyroscopeX { get; set; }
        public int GyroscopeY { get; set; }
        public int GyroscopeZ { get; set; }
        public int Temperature { get; set; }
        public long SampleReads { get; private set; }
        private void Put16(int address, int value)
        { registers[address] = (byte)(value >> 8); registers[address + 1] = (byte)value; }
        private readonly byte[] registers = new byte[256];
        private byte pointer;
    }

    public class HubBMP280 : II2CPeripheral
    {
        public HubBMP280() { Reset(); }
        public void Reset()
        {
            Array.Clear(registers, 0, registers.Length);
            registers[0xD0] = 0x58;
            // Bosch datasheet compensation example; 25.08 C and about 100653 Pa.
            var trim = new int[] {27504, 26435, -1000, 36477, -10685, 3024, 2855, 140, -7, 15500, -14600, 6000};
            for(var i = 0; i < trim.Length; ++i)
            { registers[0x88 + i * 2] = (byte)trim[i]; registers[0x89 + i * 2] = (byte)(trim[i] >> 8); }
            RawTemperature = 519888; RawPressure = 415148;
            pointer = 0; SampleReads = 0;
        }
        public void Write(byte[] data)
        {
            if(data.Length == 0) return;
            pointer = data[0];
            for(var i = 1; i < data.Length; ++i)
            {
                if(pointer == 0xE0 && data[i] == 0xB6) { Reset(); return; }
                if(pointer == 0xF4 || pointer == 0xF5) registers[pointer] = data[i];
                pointer++;
            }
        }
        public byte[] Read(int count = 1)
        {
            var result = new byte[count];
            for(var i = 0; i < count; ++i)
            {
                if(pointer == 0xF7)
                { Put20(0xF7, RawPressure); Put20(0xFA, RawTemperature); SampleReads++; }
                result[i] = registers[pointer++];
            }
            return result;
        }
        public void FinishTransmission() { }
        public int RawTemperature { get; set; }
        public int RawPressure { get; set; }
        public long SampleReads { get; private set; }
        private void Put20(int address, int value)
        { registers[address] = (byte)(value >> 12); registers[address + 1] = (byte)(value >> 4); registers[address + 2] = (byte)(value << 4); }
        private readonly byte[] registers = new byte[256];
        private byte pointer;
    }
}
