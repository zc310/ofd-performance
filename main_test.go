//go:build linux

package main

import (
	"archive/zip"
	"bytes"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"
)

// 两页尺寸不同，确认 PNG 测试只输出首页、使用 150 DPI，并保留白色背景。
func TestPNGMeasurementConvertsOnlyFirstPage(t *testing.T) {
	var archive bytes.Buffer
	writer := zip.NewWriter(&archive)
	entries := map[string]string{
		"OFD.xml":                        `<ofd:OFD xmlns:ofd="http://www.ofdspec.org/2016" Version="1.1" DocType="OFD"><ofd:DocBody><ofd:DocInfo><ofd:DocID>0123456789abcdef0123456789abcdef</ofd:DocID></ofd:DocInfo><ofd:DocRoot>Doc_0/Document.xml</ofd:DocRoot></ofd:DocBody></ofd:OFD>`,
		"Doc_0/Document.xml":             `<ofd:Document xmlns:ofd="http://www.ofdspec.org/2016"><ofd:CommonData><ofd:MaxUnitID>2</ofd:MaxUnitID><ofd:PageArea><ofd:PhysicalBox>0 0 25.4 25.4</ofd:PhysicalBox></ofd:PageArea></ofd:CommonData><ofd:Pages><ofd:Page ID="1" BaseLoc="Pages/Page_0/Content.xml"/><ofd:Page ID="2" BaseLoc="Pages/Page_1/Content.xml"/></ofd:Pages></ofd:Document>`,
		"Doc_0/Pages/Page_0/Content.xml": `<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016"><ofd:Content/></ofd:Page>`,
		"Doc_0/Pages/Page_1/Content.xml": `<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016"><ofd:Area><ofd:PhysicalBox>0 0 50.8 50.8</ofd:PhysicalBox></ofd:Area><ofd:Content/></ofd:Page>`,
	}
	for name, data := range entries {
		entry, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte(data)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	input := filepath.Join(t.TempDir(), "two-pages.ofd")
	if err := os.WriteFile(input, archive.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(t.TempDir(), "first-page.png")
	if err := runChild(input, "png", output); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	reader := bytes.NewReader(data)
	img, err := png.Decode(reader)
	if err != nil {
		t.Fatal(err)
	}
	if reader.Len() != 0 {
		t.Fatalf("首页 PNG 后仍有 %d 字节，可能输出了多页", reader.Len())
	}
	if bounds := img.Bounds(); bounds.Dx() != 150 || bounds.Dy() != 150 {
		t.Fatalf("首页像素尺寸 = %v，期望 150×150", bounds)
	}
	if got := color.NRGBAModel.Convert(img.At(0, 0)); got != (color.NRGBA{R: 255, G: 255, B: 255, A: 255}) {
		t.Fatalf("首页背景 = %v，期望不透明白色", got)
	}
}
