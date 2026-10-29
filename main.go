//go:build linux

// 本程序测量指定 OFD 库的性能，产物写入调用方指定的临时目录。
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"image/color"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/zc310/ofd/pkg/converter"
	"github.com/zc310/ofd/pkg/webreader"
)

var (
	errArguments = errors.New("无效的测量参数")
	errOutput    = errors.New("测量结果无效")
)

const measurementMethod = "ofd-process-v1"

type measurement struct {
	File        string  `json:"file"`
	Operation   string  `json:"operation"`
	ElapsedMS   float64 `json:"elapsed_ms"`
	ProcessMS   float64 `json:"process_ms"`
	PeakRSSKiB  int64   `json:"peak_rss_kib"`
	OutputBytes int     `json:"output_bytes"`
	Characters  int     `json:"characters,omitempty"`
	Pages       int     `json:"pages,omitempty"`
	Width       int     `json:"width,omitempty"`
	Height      int     `json:"height,omitempty"`
}

type sample struct {
	Path   string `json:"path"`
	Bytes  int    `json:"bytes"`
	SHA256 string `json:"sha256"`
}

func main() {
	output := flag.String("out", "", "结果目录，必须尚不存在")
	repeat := flag.Int("repeat", 10, "每项独立进程测量次数，不含一次预跑")
	font := flag.String("font", "", "受控字体环境使用的中文 TTF/OTF 字体文件")
	systemFontsOnly := flag.Bool("system-fonts-only", true, "排除用户字体，仅保留系统目录和 -font 指定的字体")
	child := flag.Bool("child", false, "内部单次测量模式")
	operation := flag.String("operation", "", "内部测量项目")
	input := flag.String("input", "", "内部测量输入")
	artifact := flag.String("artifact", "", "内部产物路径")
	flag.Parse()
	var err error
	switch {
	case *child:
		err = runChild(*input, *operation, *artifact)
	case *output == "" || *repeat < 1:
		err = fmt.Errorf("%w: 需要 -out 结果目录和正数 -repeat", errArguments)
	default:
		err = runSuite(*output, *repeat, *font, *systemFontsOnly)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func runChild(input, operation, artifact string) error {
	r := measurement{File: input, Operation: operation}
	var data []byte
	var buffer bytes.Buffer
	ctx := context.Background()
	start := time.Now()
	switch operation {
	case "open":
		reader, err := webreader.OpenInput(input, webreader.OpenOptions{})
		if err != nil {
			return err
		}
		r.Pages = reader.PageCount()
		if err := reader.Close(); err != nil {
			return err
		}
	case "png":
		if err := converter.Convert(ctx, "ofd", "png", input, &buffer,
			converter.Page(1), converter.DPI(150), converter.BgColor(color.White)); err != nil {
			return err
		}
		data = buffer.Bytes()
	case "pdf":
		if err := converter.PDF(ctx, input, &buffer, converter.DPI(150), converter.BgColor(color.White), converter.PDFParallel(false)); err != nil {
			return err
		}
		data = buffer.Bytes()
	case "text":
		if err := converter.Text(ctx, input, &buffer); err != nil {
			return err
		}
		data = buffer.Bytes()
	default:
		return fmt.Errorf("%w: 未知项目 %q", errArguments, operation)
	}
	r.ElapsedMS = float64(time.Since(start)) / float64(time.Millisecond)
	r.OutputBytes = len(data)
	// 内容检查和产物落盘均在 API 计时结束之后。
	if operation == "png" {
		config, err := png.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			return err
		}
		r.Width, r.Height = config.Width, config.Height
		// 仅在预跑完整解码校验；正式样本的峰值 RSS 不包含额外的解码图像。
		if artifact != "" {
			if _, err := png.Decode(bytes.NewReader(data)); err != nil {
				return err
			}
		}
	}
	if operation == "text" {
		if !utf8.Valid(data) {
			return fmt.Errorf("%w: 文字输出不是 UTF-8", errOutput)
		}
		r.Characters = utf8.RuneCount(data)
	}
	if artifact != "" && operation != "open" {
		if err := os.WriteFile(artifact, data, 0600); err != nil {
			return err
		}
	}
	return json.NewEncoder(os.Stdout).Encode(r)
}

func runSuite(output string, repeat int, fontFile string, systemFontsOnly bool) error {
	files := []string{
		"testdata/ofdrw/zsbk.ofd", "testdata/ofdrw/intro.ofd",
		"testdata/ofd-creator/1000-pages.ofd", "testdata/ofdrw/y.ofd",
	}
	var samples []sample
	for _, file := range files {
		data, err := os.ReadFile(file)
		if err != nil {
			return err
		}
		samples = append(samples, sample{Path: file, Bytes: len(data), SHA256: fmt.Sprintf("%x", sha256.Sum256(data))})
	}
	var fallback *sample
	var fontData []byte
	if systemFontsOnly {
		if fontFile == "" {
			return fmt.Errorf("%w: 受控字体环境需要 -font 中文 TTF/OTF 字体文件", errArguments)
		}
		data, err := os.ReadFile(fontFile)
		if err != nil {
			return err
		}
		fallback = &sample{Path: fontFile, Bytes: len(data), SHA256: fmt.Sprintf("%x", sha256.Sum256(data))}
		fontData = data
	}
	var err error
	output, err = filepath.Abs(output)
	if err != nil {
		return err
	}
	if err := os.Mkdir(output, 0755); err != nil {
		return err
	}
	environment := os.Environ()
	if systemFontsOnly {
		// 字体库按 HOME/XDG 枚举用户目录；子进程仅接收显式选择的字体。
		environment = nil
		for _, item := range os.Environ() {
			key, _, _ := strings.Cut(item, "=")
			if key != "HOME" && key != "XDG_DATA_HOME" && key != "XDG_DATA_DIRS" {
				environment = append(environment, item)
			}
		}
		dataHome := filepath.Join(output, "font-data")
		fontDir := filepath.Join(dataHome, "fonts")
		if err := os.MkdirAll(fontDir, 0755); err != nil {
			return err
		}
		if err := os.WriteFile(filepath.Join(fontDir, filepath.Base(fontFile)), fontData, 0600); err != nil {
			return err
		}
		environment = append(environment, "XDG_DATA_HOME="+dataHome)
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	var results []measurement
	for _, file := range files {
		for _, operation := range []string{"open", "png", "pdf", "text"} {
			name := filepath.Base(file) + "." + operation
			var times []float64
			for i := 0; i <= repeat; i++ {
				args := []string{"-child", "-input", file, "-operation", operation}
				// 只在预跑中落盘，正式测量的峰值 RSS 不含产物落盘。
				if i == 0 {
					args = append(args, "-artifact", filepath.Join(output, name))
				}
				command := exec.CommandContext(context.Background(), executable, args...)
				command.Env = environment
				command.Stderr = os.Stderr
				start := time.Now()
				data, err := command.Output()
				if err != nil {
					return fmt.Errorf("%s: %w", name, err)
				}
				var result measurement
				if err := json.Unmarshal(data, &result); err != nil {
					return err
				}
				result.ProcessMS = float64(time.Since(start)) / float64(time.Millisecond)
				usage, ok := command.ProcessState.SysUsage().(*syscall.Rusage)
				if !ok {
					return fmt.Errorf("%w: 无法读取 Linux 进程峰值内存", errOutput)
				}
				result.PeakRSSKiB = usage.Maxrss
				if i != 0 {
					results = append(results, result)
					times = append(times, result.ElapsedMS)
				}
			}
			sort.Float64s(times)
			median := (times[(len(times)-1)/2] + times[len(times)/2]) / 2
			fmt.Fprintf(os.Stdout, "%s: median %.3f ms (%d runs)\n", name, median, repeat)
		}
	}
	report := struct {
		Method          string        `json:"method"`
		Date            string        `json:"date"`
		GoVersion       string        `json:"go_version"`
		GOMAXPROCS      int           `json:"gomaxprocs"`
		Repeat          int           `json:"repeat"`
		SystemFontsOnly bool          `json:"system_fonts_only"`
		FallbackFont    *sample       `json:"fallback_font,omitempty"`
		Samples         []sample      `json:"samples"`
		Results         []measurement `json:"results"`
	}{measurementMethod, time.Now().UTC().Format(time.RFC3339), runtime.Version(), runtime.GOMAXPROCS(0), repeat, systemFontsOnly, fallback, samples, results}
	data, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(output, "results.json"), append(data, '\n'), 0600)
}
