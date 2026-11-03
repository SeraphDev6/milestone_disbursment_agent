import type { ReactNode } from "react";
import { Dialog as AriaDialog, Heading, Modal, ModalOverlay } from "react-aria-components";
import { Button, type Variant } from "./Button";
import s from "./Dialog.module.css";

/**
 * A modal dialog: focus is trapped inside, Escape closes it (unless `isDismissable` is false for a
 * running action), and focus returns to whatever opened it.
 */
export function Dialog({
  isOpen, onOpenChange, title, children, footer, wide, isDismissable = true, role = "dialog",
}: { isOpen: boolean; onOpenChange: (o: boolean) => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean; isDismissable?: boolean; role?: "dialog" | "alertdialog" }) {
  return (
    <ModalOverlay className={s.overlay} isOpen={isOpen} onOpenChange={onOpenChange} isDismissable={isDismissable} isKeyboardDismissDisabled={!isDismissable}>
      <Modal className={`${s.modal} ${wide ? s.wide : ""}`}>
        <AriaDialog className={s.dialog} role={role}>
          {({ close }) => (
            <>
              <div className={s.head}>
                <Heading slot="title">{title}</Heading>
                {isDismissable && <Button variant="quiet" size="sm" icon="close" aria-label="Close" onPress={close} />}
              </div>
              {children}
              {footer && <div className={s.footer}>{footer}</div>}
            </>
          )}
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}

/**
 * Confirmation for money-moving or irreversible actions. `summary` states plainly what will happen,
 * e.g. "Release USD 20,000.00 to Riverside Reads". Uses role=alertdialog so it's announced as urgent.
 */
export function ConfirmDialog({
  isOpen, onOpenChange, title, summary, children, confirmLabel, confirmVariant = "primary", onConfirm, isPending, confirmDisabled,
}: {
  isOpen: boolean;
  onOpenChange: (o: boolean) => void;
  title: ReactNode;
  summary: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  confirmVariant?: Variant;
  onConfirm: () => void;
  isPending?: boolean;
  confirmDisabled?: boolean;
}) {
  return (
    <Dialog
      isOpen={isOpen}
      onOpenChange={onOpenChange}
      title={title}
      role="alertdialog"
      isDismissable={!isPending}
      footer={
        <>
          <Button variant="quiet" onPress={() => onOpenChange(false)} isDisabled={isPending}>Cancel</Button>
          <Button variant={confirmVariant} onPress={onConfirm} isPending={isPending} isDisabled={confirmDisabled}>{confirmLabel}</Button>
        </>
      }
    >
      <div className={s.summary}>{summary}</div>
      {children}
    </Dialog>
  );
}
