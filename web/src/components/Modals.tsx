import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useEffect, useState } from "react";

export function RenameModal(props: { visible: boolean; initial: string; onDismiss: () => void; onSave: (t: string) => void }) {
  const [value, setValue] = useState(props.initial);
  useEffect(() => setValue(props.initial), [props.initial, props.visible]);
  const ok = value.trim().length > 0;
  return (
    <Modal
      visible={props.visible}
      onDismiss={props.onDismiss}
      header="Rename chat"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={props.onDismiss}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!ok} onClick={() => props.onSave(value.trim())}>
              Save
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <form onSubmit={(e) => { e.preventDefault(); if (ok) props.onSave(value.trim()); }}>
        <FormField label="Name">
          <Input value={value} onChange={({ detail }) => setValue(detail.value)} autoFocus />
        </FormField>
      </form>
    </Modal>
  );
}

export function ConfirmDelete(props: { visible: boolean; title: string; onDismiss: () => void; onConfirm: () => void }) {
  return (
    <Modal
      visible={props.visible}
      onDismiss={props.onDismiss}
      header="Delete chat?"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={props.onDismiss}>
              Cancel
            </Button>
            <Button variant="primary" onClick={props.onConfirm}>
              Delete
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <b>{props.title}</b> and its messages will be deleted. Its rows stay in the request log.
    </Modal>
  );
}
