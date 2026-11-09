type SliderProps = {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit: string
  onChange: (value: number) => void
  disabled?: boolean
}

export function Slider({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
  disabled = false,
}: SliderProps) {
  return (
    <label className="control-field">
      <span>
        {label}
        <output>{value.toLocaleString()} {unit}</output>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  )
}
