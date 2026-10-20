// Register readback extension for the hub's GPIO configuration tests.
// Renode's STM32_GPIOPort tags OTYPER bits without storing their values.
// Preserve firmware writes here; pin voltages/open-drain electrical behavior
// are deliberately outside this model. Other registers use the stock model.
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.Bus;

namespace Antmicro.Renode.Peripherals.GPIOPort
{
    public class HubGPIOPort : STM32_GPIOPort, IDoubleWordPeripheral
    {
        public HubGPIOPort(IMachine machine, uint modeResetValue = 0,
            uint outputSpeedResetValue = 0, uint pullUpPullDownResetValue = 0,
            uint numberOfAFs = 16)
            : base(machine, modeResetValue, outputSpeedResetValue,
                   pullUpPullDownResetValue, numberOfAFs) { }

        public new uint ReadDoubleWord(long offset)
        {
            return offset == 0x04 ? outputType : base.ReadDoubleWord(offset);
        }

        public new void WriteDoubleWord(long offset, uint value)
        {
            if(offset == 0x04) outputType = value & 0xFFFF;
            else base.WriteDoubleWord(offset, value);
        }

        public override void Reset()
        {
            base.Reset();
            outputType = 0;
        }

        private uint outputType;
    }
}
