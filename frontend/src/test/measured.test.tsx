import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { api, ApiError } from "../api/client";
import { Measured } from "../pages/Measured";
import { instrumentProfile, measuredPrediction } from "./fixtures";

vi.mock("../components/Chart", () => ({
  SpectrumChart: () => <div aria-label="Спектральный график загруженного измерения" />,
  Chart: () => <div aria-label="График" />,
}));

function wrap(element: React.ReactNode, route = "/measured") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function makeValidCsvFile(): File {
  const csvContent =
    "wavelength_nm,intensity\n240.0,1.2\n240.1,1.5\n240.2,1.3\n";
  return new File([csvContent], "measured_spectrum.csv", { type: "text/csv" });
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "instrumentProfiles").mockResolvedValue([instrumentProfile]);
  vi.spyOn(api, "exportMeasured").mockResolvedValue();
});

describe("Measured page scenarios", () => {
  it("loads profiles and displays profile requirements card", async () => {
    wrap(<Measured />);
    expect(
      await screen.findByText("calcium-v4 (готов)"),
    ).toBeInTheDocument();
    expect(screen.getByText("Ожидаемый прибор:")).toBeInTheDocument();
    expect(screen.getByText("chemcam-lanl-testbed")).toBeInTheDocument();
    expect(screen.getByText("Поддерживаемые элементы:")).toBeInTheDocument();
    expect(screen.getByText("Ca")).toBeInTheDocument();
  });

  it("handles profile loading error cleanly", async () => {
    vi.spyOn(api, "instrumentProfiles").mockRejectedValue(
      new Error("Каталог профилей недоступен"),
    );
    wrap(<Measured />);
    expect(
      await screen.findByText("Каталог профилей недоступен"),
    ).toBeInTheDocument();
  });

  it("blocks submission when mandatory fields are missing and displays inline errors", async () => {
    const createSpy = vi.spyOn(api, "createMeasuredPrediction");
    wrap(<Measured />);

    await screen.findByText("calcium-v4 (готов)");

    const submitBtn = screen.getByRole("button", {
      name: "Выполнить анализ измерения",
    });
    await userEvent.click(submitBtn);

    expect(createSpy).not.toHaveBeenCalled();
    expect(
      screen.getByText("Загрузите файл спектра в формате CSV."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Укажите идентификатор прибора измерения."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Требуется явное подтверждение единиц шкалы (нм / nm)."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Выберите среду измерения: воздух (air) или вакуум (vacuum)."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Укажите источник калибровки или описание шкалы длин волн."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Укажите SHA-256 хеш источника калибровки шкалы."),
    ).toBeInTheDocument();
  });

  it("does NOT silently fill instrument_id from profile without user confirmation", async () => {
    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");

    const instInput = screen.getByPlaceholderText(
      "например: chemcam-lanl-testbed",
    ) as HTMLInputElement;
    expect(instInput.value).toBe("");

    // Clicking explicit helper button populates it
    const helperBtn = screen.getByRole("button", {
      name: /Подставить прибор профиля/,
    });
    await userEvent.click(helperBtn);
    expect(instInput.value).toBe("chemcam-lanl-testbed");
  });

  it("requires explicit units confirmation and does not select medium silently", async () => {
    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");

    const unitsCheck = screen.getByRole("checkbox", {
      name: /Подтверждаю, что длина волны в CSV представлена в нм/i,
    }) as HTMLInputElement;
    expect(unitsCheck.checked).toBe(false);

    const mediumSelect = screen.getByRole("combobox", {
      name: /Среда измерения шкалы/i,
    }) as HTMLSelectElement;
    expect(mediumSelect.value).toBe("");
  });

  it("submits valid measured form and renders results with elements, scores, and unknown reasons", async () => {
    const createSpy = vi
      .spyOn(api, "createMeasuredPrediction")
      .mockResolvedValue(measuredPrediction);

    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");

    // 1. Upload CSV
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = makeValidCsvFile();
    await userEvent.upload(fileInput, file);

    // 2. Set instrument via explicit confirmation
    await userEvent.click(
      screen.getByRole("button", { name: /Подставить прибор профиля/ }),
    );

    // 3. Confirm units
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что длина волны в CSV представлена в нм/i,
      }),
    );

    // 4. Select medium
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Среда измерения шкалы/i }),
      "air",
    );

    // 5. Fill source & source_sha256
    await userEvent.type(
      screen.getByPlaceholderText(/ChemCam Testbed Calibration Protocol/),
      "ChemCam-Protocol-v2",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/64 hex-символа SHA-256/),
      "c".repeat(64),
    );

    // Submit
    await userEvent.click(
      screen.getByRole("button", { name: "Выполнить анализ измерения" }),
    );

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));

    // Inspect result rendering
    expect(
      await screen.findByText(`ID: ${measuredPrediction.prediction_id}`),
    ).toBeInTheDocument();
    expect(screen.getByText("Обнаружен")).toBeInTheDocument();
    expect(screen.getByText("0.8521")).toBeInTheDocument(); // Ca score
    expect(screen.getByText("Не обнаружен")).toBeInTheDocument(); // Fe
    expect(screen.getByText("0.1245")).toBeInTheDocument(); // Fe score

    // Check unknown element handling
    expect(screen.getByText("Не поддерживается")).toBeInTheDocument(); // H status
    expect(
      screen.getByText(
        "Элемент не входит в набор определяемых данным приборным профилем",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("код: unsupported_by_profile")).toBeInTheDocument();

    // Check refusal reason code and explanation
    expect(
      screen.getByText(
        "Низкое отношение сигнал/шум аналитических линий (недостаточно для надёжного подтверждения)",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("код: low_snr")).toBeInTheDocument();
  });

  it("restores result from URL prediction parameter", async () => {
    vi.spyOn(api, "measuredPrediction").mockResolvedValue(measuredPrediction);
    wrap(<Measured />, `/measured?prediction=${measuredPrediction.prediction_id}`);

    expect(
      await screen.findByText(`ID: ${measuredPrediction.prediction_id}`),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("chemcam-lanl-testbed").length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("air → vacuum")).toBeInTheDocument();
  });

  it("displays server 422 errors clearly beside the form", async () => {
    vi.spyOn(api, "createMeasuredPrediction").mockRejectedValue(
      new ApiError(
        "Measurement instrument differs from profile: expected 'chemcam-lanl-testbed', got 'other-spectrometer'",
        "unprocessable_entity",
        422,
      ),
    );

    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");

    // Fill minimum fields with incompatible instrument
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(fileInput, makeValidCsvFile());

    await userEvent.type(
      screen.getByPlaceholderText(/например: chemcam-lanl-testbed/),
      "other-spectrometer",
    );
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что длина волны в CSV представлена в нм/i,
      }),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Среда измерения шкалы/i }),
      "vacuum",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/ChemCam Testbed Calibration Protocol/),
      "calib-source",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/64 hex-символа SHA-256/),
      "a".repeat(64),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Выполнить анализ измерения" }),
    );

    expect(
      await screen.findByText(
        /Measurement instrument differs from profile/i,
      ),
    ).toBeInTheDocument();
  });

  it("triggers export for json, csv, and zip formats", async () => {
    vi.spyOn(api, "measuredPrediction").mockResolvedValue(measuredPrediction);
    const exportSpy = vi.spyOn(api, "exportMeasured").mockResolvedValue();

    wrap(<Measured />, `/measured?prediction=${measuredPrediction.prediction_id}`);
    await screen.findByText(`ID: ${measuredPrediction.prediction_id}`);

    await userEvent.click(screen.getByRole("button", { name: "Экспорт JSON" }));
    expect(exportSpy).toHaveBeenCalledWith(measuredPrediction.prediction_id, "json");

    await userEvent.click(screen.getByRole("button", { name: "Экспорт CSV" }));
    expect(exportSpy).toHaveBeenCalledWith(measuredPrediction.prediction_id, "csv");

    await userEvent.click(screen.getByRole("button", { name: "Экспорт ZIP" }));
    expect(exportSpy).toHaveBeenCalledWith(measuredPrediction.prediction_id, "zip");
  });

  it("shows warning when inputs are modified after receiving analysis result", async () => {
    vi.spyOn(api, "createMeasuredPrediction").mockResolvedValue(measuredPrediction);
    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(fileInput, makeValidCsvFile());
    await userEvent.click(
      screen.getByRole("button", { name: /Подставить прибор профиля/ }),
    );
    await userEvent.click(
      screen.getByRole("checkbox", {
        name: /Подтверждаю, что длина волны в CSV представлена в нм/i,
      }),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Среда измерения шкалы/i }),
      "vacuum",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/ChemCam Testbed Calibration Protocol/),
      "source-v1",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/64 hex-символа SHA-256/),
      "b".repeat(64),
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Выполнить анализ измерения" }),
    );
    await screen.findByText(`ID: ${measuredPrediction.prediction_id}`);

    // Now change one of the inputs
    const sourceInput = screen.getByPlaceholderText(
      /ChemCam Testbed Calibration Protocol/,
    );
    await userEvent.clear(sourceInput);
    await userEvent.type(sourceInput, "source-v2-modified");

    expect(
      screen.getByText(/Входные данные изменены:/),
    ).toBeInTheDocument();
  });
});


describe("Measured provenance and asynchronous input safety", () => {
  it("does not assume vacuum before the user confirms the axis medium", async () => {
    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");
    await userEvent.upload(screen.getByLabelText(/Файл спектра/), makeValidCsvFile());
    expect(await screen.findByText("Подтвердите шкалу спектра")).toBeInTheDocument();
    expect(screen.queryByLabelText("Спектральный график загруженного измерения")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Среда измерения шкалы/ }), "air");
    expect(screen.getByLabelText("Спектральный график загруженного измерения")).toBeInTheDocument();
  });

  it("ignores a stale CSV parse completing after a newer file", async () => {
    let finishFirst!: (value: client.ParsedMeasuredCsv) => void;
    const second = { wavelength_nm: [250, 251, 252], intensity: [1, 1, 1], pointCount: 3, minWavelength: 250, maxWavelength: 252, minIntensity: 1, maxIntensity: 1 };
    vi.spyOn(client, "validateMeasuredCsv")
      .mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }))
      .mockResolvedValueOnce(second);
    wrap(<Measured />);
    await screen.findByText("calcium-v4 (готов)");
    const input = screen.getByLabelText(/Файл спектра/);
    await userEvent.upload(input, new File(["first"], "first.csv"));
    expect(screen.getByRole("button", { name: "Выполнить анализ измерения" })).toBeDisabled();
    await userEvent.upload(input, new File(["second"], "second.csv"));
    expect(await screen.findByText(/Загружено 3 точек · 250.0–252.0/)).toBeInTheDocument();
    await act(async () => finishFirst({ ...second, minWavelength: 100, maxWavelength: 102 }));
    expect(screen.getByText(/Загружено 3 точек · 250.0–252.0/)).toBeInTheDocument();
    expect(screen.queryByText(/100.0–102.0/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выполнить анализ измерения" })).toBeEnabled();
  });

  it("keeps a draft CSV graph out of a saved result opened by URL", async () => {
    vi.spyOn(api, "measuredPrediction").mockResolvedValue(measuredPrediction);
    wrap(<Measured />, `/measured?prediction=${measuredPrediction.prediction_id}`);
    await screen.findByText(`ID: ${measuredPrediction.prediction_id}`);
    await userEvent.upload(screen.getByLabelText(/Файл спектра/), makeValidCsvFile());
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Среда измерения шкалы/ }), "vacuum");
    expect(screen.queryByLabelText("Спектральный график загруженного измерения")).not.toBeInTheDocument();
    expect(screen.getByText(/График текущего черновика не подменяет сохранённый результат/)).toBeInTheDocument();
  });

  it("shows export failures without losing the saved result", async () => {
    vi.spyOn(api, "measuredPrediction").mockResolvedValue(measuredPrediction);
    vi.spyOn(api, "exportMeasured").mockRejectedValue(new Error("Экспорт временно недоступен"));
    wrap(<Measured />, `/measured?prediction=${measuredPrediction.prediction_id}`);
    await screen.findByText(`ID: ${measuredPrediction.prediction_id}`);
    await userEvent.click(screen.getByRole("button", { name: "Экспорт ZIP" }));
    expect(await screen.findByText("Экспорт временно недоступен")).toBeInTheDocument();
    expect(screen.getByText(`ID: ${measuredPrediction.prediction_id}`)).toBeInTheDocument();
  });
});
