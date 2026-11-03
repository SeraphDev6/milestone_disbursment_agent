/**
 * Form fields. Every field has a visible label, optional description, and an error tied to the input
 * via aria-describedby (React Aria wires this). `id` lets an ErrorSummary link straight to the field.
 */
import type { ReactNode } from "react";
import {
  Button as AriaButton,
  Checkbox as AriaCheckbox,
  CheckboxGroup as AriaCheckboxGroup,
  DateField as AriaDateField,
  DateInput,
  DateSegment,
  FieldError,
  Group,
  Input,
  Label,
  ListBox,
  ListBoxItem,
  NumberField as AriaNumberField,
  Popover,
  Radio as AriaRadio,
  RadioGroup as AriaRadioGroup,
  Select as AriaSelect,
  SelectValue,
  Switch as AriaSwitch,
  Text,
  TextArea as AriaTextArea,
  TextField as AriaTextField,
  type Key,
} from "react-aria-components";
import { parseDate, type CalendarDate } from "@internationalized/date";
import { Glyph } from "./Glyph";
import s from "./Field.module.css";

type Base = {
  label: ReactNode;
  description?: ReactNode;
  /** Error text; when set the field is invalid. */
  error?: string | null;
  optional?: boolean;
  id?: string;
  className?: string;
  isDisabled?: boolean;
  isRequired?: boolean;
};

function LabelText({ label, optional }: { label: ReactNode; optional?: boolean }) {
  return (
    <Label className={s.label}>
      {label}
      {optional && <span className={s.optional}>(optional)</span>}
    </Label>
  );
}

function Help({ description, error }: { description?: ReactNode; error?: string | null }) {
  return (
    <>
      {description && <Text slot="description" className={s.description}>{description}</Text>}
      <FieldError className={s.error}>
        <Glyph name="warning" size={14} />
        <span>{error}</span>
      </FieldError>
    </>
  );
}

export function TextField({
  label, description, error, optional, id, className, value, onChange, type = "text", placeholder, autoComplete, inputMode, maxLength, isDisabled, isRequired, figures, name, autoFocus,
}: Base & {
  value: string;
  onChange: (v: string) => void;
  type?: "text" | "email" | "url" | "search" | "password";
  placeholder?: string;
  autoComplete?: string;
  inputMode?: "text" | "email" | "url" | "numeric" | "decimal";
  maxLength?: number;
  figures?: boolean;
  name?: string;
  autoFocus?: boolean;
}) {
  return (
    <AriaTextField
      className={`${s.field} ${className ?? ""}`}
      value={value}
      onChange={onChange}
      type={type}
      isInvalid={!!error}
      isDisabled={isDisabled}
      isRequired={isRequired}
      maxLength={maxLength}
      autoComplete={autoComplete}
      inputMode={inputMode}
      name={name}
      validationBehavior="aria"
      autoFocus={autoFocus}
    >
      <LabelText label={label} optional={optional} />
      <Input id={id} className={`${s.input} ${figures ? s.figures : ""}`} placeholder={placeholder} />
      <Help description={description} error={error} />
    </AriaTextField>
  );
}

export function TextArea({
  label, description, error, optional, id, className, value, onChange, rows = 4, maxLength, isDisabled, isRequired, placeholder,
}: Base & { value: string; onChange: (v: string) => void; rows?: number; maxLength?: number; placeholder?: string }) {
  return (
    <AriaTextField className={`${s.field} ${className ?? ""}`} value={value} onChange={onChange} isInvalid={!!error} isDisabled={isDisabled} isRequired={isRequired} maxLength={maxLength} validationBehavior="aria">
      <LabelText label={label} optional={optional} />
      <AriaTextArea id={id} className={s.input} rows={rows} placeholder={placeholder} />
      <Help description={description} error={error} />
      {maxLength && (
        <Text slot="description" className={s.description} aria-live="polite">
          {value.length > maxLength * 0.8 ? `${maxLength - value.length} characters left` : ""}
        </Text>
      )}
    </AriaTextField>
  );
}

/** Numbers and money. Money is held as a decimal string so cents never drift through floats. */
export function NumberField({
  label, description, error, optional, id, className, value, onChange, prefix, minValue, maxValue, step, fractionDigits, isDisabled, isRequired, percent,
}: Base & {
  value: number | null;
  onChange: (v: number | null) => void;
  prefix?: string;
  minValue?: number;
  maxValue?: number;
  step?: number;
  fractionDigits?: number;
  percent?: boolean;
}) {
  return (
    <AriaNumberField
      className={`${s.field} ${className ?? ""}`}
      value={value ?? Number.NaN}
      onChange={(v) => onChange(Number.isNaN(v) ? null : v)}
      minValue={minValue}
      maxValue={maxValue}
      step={step}
      isInvalid={!!error}
      isDisabled={isDisabled}
      isRequired={isRequired}
      validationBehavior="aria"
      formatOptions={
        percent
          ? { style: "percent", maximumFractionDigits: 0 }
          : fractionDigits !== undefined
            ? { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits, useGrouping: true }
            : { maximumFractionDigits: 6 }
      }
    >
      <LabelText label={label} optional={optional} />
      <Group className={s.group}>
        {prefix && <span className={s.prefix} aria-hidden="true">{prefix}</span>}
        <Input id={id} className={`${s.input} ${s.bare} ${s.figures}`} />
      </Group>
      <Help description={description} error={error} />
    </AriaNumberField>
  );
}

/** A calendar date as YYYY-MM-DD (what the API speaks). Keyboard: type or arrow through segments. */
export function DateField({
  label, description, error, optional, id, className, value, onChange, isDisabled, isRequired,
}: Base & { value: string | null; onChange: (v: string | null) => void }) {
  let parsed: CalendarDate | null = null;
  try {
    parsed = value ? parseDate(value) : null;
  } catch {
    parsed = null;
  }
  return (
    <AriaDateField
      className={`${s.field} ${className ?? ""}`}
      value={parsed}
      onChange={(d) => onChange(d ? d.toString() : null)}
      isInvalid={!!error}
      isDisabled={isDisabled}
      isRequired={isRequired}
      validationBehavior="aria"
      id={id}
    >
      <LabelText label={label} optional={optional} />
      <DateInput className={s.group}>{(seg) => <DateSegment segment={seg} className={s.segment} />}</DateInput>
      <Help description={description} error={error} />
    </AriaDateField>
  );
}

export type Option<K extends string = string> = { id: K; label: string; description?: string; isDisabled?: boolean };

export function Select<K extends string>({
  label, description, error, optional, id, className, value, onChange, options, placeholder = "Choose…", isDisabled, isRequired,
}: Base & { value: K | null; onChange: (v: K) => void; options: Option<K>[]; placeholder?: string }) {
  return (
    <AriaSelect
      className={`${s.field} ${className ?? ""}`}
      selectedKey={value}
      onSelectionChange={(k: Key | null) => k !== null && onChange(k as K)}
      isInvalid={!!error}
      isDisabled={isDisabled}
      isRequired={isRequired}
      placeholder={placeholder}
      validationBehavior="aria"
      disabledKeys={options.filter((o) => o.isDisabled).map((o) => o.id)}
    >
      <LabelText label={label} optional={optional} />
      <AriaButton id={id} className={s.trigger}>
        <SelectValue>{({ isPlaceholder, selectedText, defaultChildren }) => (isPlaceholder ? defaultChildren : selectedText)}</SelectValue>
        <Glyph name="chevronDown" />
      </AriaButton>
      <Help description={description} error={error} />
      <Popover className={s.popover} offset={4}>
        <ListBox className={s.listbox} items={options}>
          {(o) => (
            <ListBoxItem id={o.id} className={s.option} textValue={o.label}>
              <Glyph name="check" className={s.check} />
              <span>
                <Text slot="label">{o.label}</Text>
                {o.description && <Text slot="description" className={s.optionDesc}>{o.description}</Text>}
              </span>
            </ListBoxItem>
          )}
        </ListBox>
      </Popover>
    </AriaSelect>
  );
}

export function Checkbox({ children, hint, isSelected, onChange, isDisabled, isInvalid, value }: { children: ReactNode; hint?: ReactNode; isSelected?: boolean; onChange?: (v: boolean) => void; isDisabled?: boolean; isInvalid?: boolean; value?: string }) {
  return (
    <AriaCheckbox className={s.choice} isSelected={isSelected} onChange={onChange} isDisabled={isDisabled} isInvalid={isInvalid} value={value}>
      <span className={s.box} aria-hidden="true"><Glyph name="check" size={14} strokeWidth={2.2} /></span>
      <span className={s.choiceText}>
        <span>{children}</span>
        {hint && <span className={s.choiceHint}>{hint}</span>}
      </span>
    </AriaCheckbox>
  );
}

export function CheckboxGroup<K extends string>({
  label, description, error, optional, className, value, onChange, options, isDisabled, isRequired, id,
}: Base & { value: K[]; onChange: (v: K[]) => void; options: Option<K>[] }) {
  return (
    <AriaCheckboxGroup id={id} className={`${s.choiceGroup} ${className ?? ""}`} value={value} onChange={(v) => onChange(v as K[])} isInvalid={!!error} isDisabled={isDisabled} isRequired={isRequired} validationBehavior="aria">
      <LabelText label={label} optional={optional} />
      <div className={s.options}>
        {options.map((o) => (
          <Checkbox key={o.id} value={o.id} isDisabled={o.isDisabled} hint={o.description}>{o.label}</Checkbox>
        ))}
      </div>
      <Help description={description} error={error} />
    </AriaCheckboxGroup>
  );
}

export function RadioGroup<K extends string>({
  label, description, error, optional, className, value, onChange, options, isDisabled, isRequired, orientation = "vertical", id,
}: Base & { value: K | null; onChange: (v: K) => void; options: Option<K>[]; orientation?: "vertical" | "horizontal" }) {
  return (
    <AriaRadioGroup id={id} className={`${s.choiceGroup} ${className ?? ""}`} value={value} onChange={(v) => onChange(v as K)} isInvalid={!!error} isDisabled={isDisabled} isRequired={isRequired} orientation={orientation} validationBehavior="aria">
      <LabelText label={label} optional={optional} />
      <div className={orientation === "horizontal" ? s.options : s.stack}>
        {options.map((o) => (
          <AriaRadio key={o.id} value={o.id} className={s.choice} isDisabled={o.isDisabled}>
            <span className={s.dot} aria-hidden="true" />
            <span className={s.choiceText}>
              <span>{o.label}</span>
              {o.description && <span className={s.choiceHint}>{o.description}</span>}
            </span>
          </AriaRadio>
        ))}
      </div>
      <Help description={description} error={error} />
    </AriaRadioGroup>
  );
}

export function Switch({ children, hint, isSelected, onChange, isDisabled }: { children: ReactNode; hint?: ReactNode; isSelected: boolean; onChange: (v: boolean) => void; isDisabled?: boolean }) {
  return (
    <AriaSwitch className={s.choice} isSelected={isSelected} onChange={onChange} isDisabled={isDisabled}>
      <span className={s.track} aria-hidden="true" />
      <span className={s.choiceText}>
        <span>{children}</span>
        {hint && <span className={s.choiceHint}>{hint}</span>}
      </span>
    </AriaSwitch>
  );
}
