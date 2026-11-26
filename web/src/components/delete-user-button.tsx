"use client";

import { Button } from "@/components/ui/button";

type DeleteUserButtonProps = {
  userId: number;
  displayName: string;
  disabled?: boolean;
};

/** 渲染用户删除按钮：提交前二次确认，避免误删账号。 */
export function DeleteUserButton({ userId, displayName, disabled = false }: DeleteUserButtonProps) {
  return (
    <form
      action="/settings/users/delete"
      method="post"
      onSubmit={(event) => {
        if (!window.confirm(`确定删除用户「${displayName}」？将物理删除账号且不可恢复。`)) {
          event.preventDefault();
        }
      }}
    >
      <input type="hidden" name="user_id" value={userId} />
      <Button
        type="submit"
        variant="outline"
        size="sm"
        className="border-rose-200 text-rose-700 hover:bg-rose-50"
        disabled={disabled}
      >
        删除
      </Button>
    </form>
  );
}
