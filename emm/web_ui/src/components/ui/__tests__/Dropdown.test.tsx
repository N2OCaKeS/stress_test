import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { Dropdown } from "../Dropdown";

it("ищет и сортирует по имени, не по внутреннему идентификатору", () => {
  function Pick() {
    const [value, setValue] = useState("");
    return <Dropdown mode="single" value={value} onChange={setValue} label="Стенд" options={[
      { value: "id_a", label: "Стенд 10" }, { value: "id_z", label: "Стенд 2" },
    ]} />;
  }
  render(<Pick />);
  fireEvent.click(screen.getByRole("button", { name: "Стенд: Все" }));
  expect(screen.getAllByRole("option").map((item) => item.textContent)).toEqual(["Стенд 2", "Стенд 10"]);
  fireEvent.change(screen.getByPlaceholderText("Поиск…"), { target: { value: "id_z" } });
  expect(screen.queryAllByRole("option")).toHaveLength(0);
  fireEvent.change(screen.getByPlaceholderText("Поиск…"), { target: { value: "стенд 2" } });
  fireEvent.click(screen.getByRole("option", { name: "Стенд 2" }));
  expect(screen.getByRole("button", { name: "Стенд: Стенд 2" })).toBeInTheDocument();
});

it("не включает недоступные записи при выборе всех", () => {
  function Pick() {
    const [value, setValue] = useState(new Set<string>());
    return <Dropdown mode="multi" value={value} onChange={setValue} options={[
      { value: "blocked", label: "Истёкший", disabled: true }, { value: "active", label: "Рабочий" },
    ]} />;
  }
  render(<Pick />);
  fireEvent.click(screen.getByRole("button", { name: "Все" }));
  fireEvent.click(screen.getByRole("button", { name: "Выбрать все" }));
  expect(screen.getByRole("checkbox", { name: "Истёкший" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Рабочий" })).toBeChecked();
});
