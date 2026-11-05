import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  type TransferApplyResponse,
  type TransferProfilesListResponse,
} from "../api/client";
import { CalibrationTransferCard } from "../components/CalibrationTransferCard";

vi.mock("../components/Chart", () => ({
  Chart: () => <div aria-label="График калибровочного переноса" />,
}));

const mockProfilesList: TransferProfilesListResponse = {
  profiles: [
    {
      profile_id: "identity",
      name: "Без коррекции (Сырой спектр)",
      method: "identity",
      source_instrument: "master",
      target_instrument: "slave",
      window_half_width: 0,
      alpha: 0.0,
      description: "Прямое пропускание",
    },
    {
      profile_id: "chemcam_pds_v1",
      name: "ChemCam PDS Transfer",
      method: "pds",
      source_instrument: "lab",
      target_instrument: "flight",
      window_half_width: 3,
      alpha: 1e-3,
      description: "PDS матрица для ChemCam",
    },
    {
      profile_id: "wavelength_alignment_v1",
      name: "Выравнивание длин волн",
      method: "wavelength_alignment",
      source_instrument: "reference",
      target_instrument: "shifted",
      window_half_width: 0,
      alpha: 0.0,
      description: "Полиномиальное совмещение",
    },
  ],
};

const mockApplyResponse: TransferApplyResponse = {
  wavelength_nm: [300.0, 350.0, 400.0, 450.0],
  intensity: [12.0, 25.0, 38.0, 15.0],
  profile_id: "chemcam_pds_v1",
  method: "pds",
  is_identity: false,
  diagnostics: {
    shift_detected_nm: 0.045,
    rmsd_change: 2.35,
    mean_correction_factor: 1.052,
    window_half_width: 3,
    alpha: 0.001,
    applied_method: "pds",
    condition_number: 1.24,
  },
  original_point_count: 4,
  corrected_point_count: 4,
};

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

describe("CalibrationTransferCard", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders calibration transfer card with profile selector and diagnostics", async () => {
    vi.spyOn(api, "transferProfiles").mockResolvedValue(mockProfilesList);
    vi.spyOn(api, "transferApply").mockResolvedValue(mockApplyResponse);

    renderWithClient(
      <CalibrationTransferCard
        spectrumId="test-spec-id"
        wavelengthNm={[300.0, 350.0, 400.0, 450.0]}
        intensity={[10.0, 22.0, 35.0, 14.0]}
      />,
    );

    expect(
      screen.getByText("Калибровочный перенос спектра (Calibration Transfer / PDS)"),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("transfer-profile-select")).toBeInTheDocument();
    });

    // Check diagnostics
    await waitFor(() => {
      expect(screen.getByText("+0.045 нм")).toBeInTheDocument();
      expect(screen.getByText("2.35")).toBeInTheDocument();
      expect(screen.getByText("1.052×")).toBeInTheDocument();
      expect(screen.getByText("PDS стандартизация")).toBeInTheDocument();
    });
  });

  it("switches profile and triggers transferApply API call", async () => {
    vi.spyOn(api, "transferProfiles").mockResolvedValue(mockProfilesList);
    const applySpy = vi
      .spyOn(api, "transferApply")
      .mockResolvedValue(mockApplyResponse);

    renderWithClient(
      <CalibrationTransferCard
        spectrumId="test-spec-id"
        wavelengthNm={[300.0, 350.0, 400.0, 450.0]}
        intensity={[10.0, 20.0, 30.0, 40.0]}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByRole("option", { name: "ChemCam PDS Transfer" }),
      ).toBeInTheDocument();
    });

    const select = screen.getByTestId("transfer-profile-select");
    await userEvent.selectOptions(select, "chemcam_pds_v1");

    await waitFor(() => {
      expect(applySpy).toHaveBeenCalledWith(
        expect.objectContaining({ profile_id: "chemcam_pds_v1" }),
        expect.anything(),
      );
    });
  });

  it("toggles difference curve view", async () => {
    vi.spyOn(api, "transferProfiles").mockResolvedValue(mockProfilesList);
    vi.spyOn(api, "transferApply").mockResolvedValue(mockApplyResponse);

    renderWithClient(
      <CalibrationTransferCard
        spectrumId="test-spec-id"
        wavelengthNm={[300.0, 350.0, 400.0, 450.0]}
        intensity={[10.0, 20.0, 30.0, 40.0]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("toggle-diff-btn")).toBeInTheDocument();
    });

    const toggleBtn = screen.getByTestId("toggle-diff-btn");
    expect(toggleBtn).toHaveTextContent("Показать разность (Δ)");

    await userEvent.click(toggleBtn);
    expect(toggleBtn).toHaveTextContent("Скрыть разность (Δ)");
  });

  it("handles transfer apply error gracefully", async () => {
    vi.spyOn(api, "transferProfiles").mockResolvedValue(mockProfilesList);
    vi.spyOn(api, "transferApply").mockRejectedValue(
      new Error("Transfer matrix inversion failure"),
    );

    renderWithClient(
      <CalibrationTransferCard
        spectrumId="test-spec-id"
        wavelengthNm={[300.0, 350.0, 400.0, 450.0]}
        intensity={[10.0, 20.0, 30.0, 40.0]}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId("transfer-error")).toBeInTheDocument();
    });
    expect(
      screen.getByText(/Transfer matrix inversion failure/),
    ).toBeInTheDocument();
  });
});
